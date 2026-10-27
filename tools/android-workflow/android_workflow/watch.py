"""PR Watcher: after the PR opens, follow it until it is merged.

Everything mechanical lives here, with no agent in the loop: read the PR (comments, review threads,
reviews, checks, reviewers, mergeability) in one GraphQL call, decide the next action, post replies,
re-run a flaky check, bring the base branch in, and merge. An agent only reads what people wrote and
decides what to do about it (`aw-pr-watcher`).

The next action is one of:
- `respond`: new comments or failing checks to handle (written in full to `RUN/pr-inbox.json`);
- `sync`: the base moved on (`behind`) or the branch conflicts with it;
- `merge`: every check this PR needs passed and the PR is approved (GitHub's review decision, with
  no change request open); a reviewer still requested does not hold an approved PR back unless
  `watch.wait_for_requested_reviewers` is set;
- `wait`: checks still run or reviewers have not answered yet (`waiting_for` says which);
- `needs_user`: too many fix rounds; `merged` / `closed`: the PR is over.

Threads are answered, never resolved: the reviewer resolves them. Merging respects the repository's own
rules (branch protection, required checks) and never bypasses them.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from android_workflow.paths import cache_dir, run_dir

WATCH_FILE = "pr-watch.json"
INBOX_FILE = "pr-inbox.json"
CI_LOG_DIR = "pr-ci"
DEFAULT_WATCH = {
    "auto_merge": True,
    # Without any reviewer requested, wait this long before merging, so a reviewer added right after the
    # PR opened is not raced. `require_review: true` always waits for an approval.
    "unreviewed_merge_after_minutes": 30,
    "require_review": False,
    # An approved PR merges although another requested reviewer has not answered yet; true waits for them all.
    "wait_for_requested_reviewers": False,
    # A check that is not required and still pending stops blocking this long after the last push.
    "optional_check_grace_minutes": 30,
    "merge_method": None,  # squash | merge | rebase; default: the first the repository allows, in that order
    "max_rounds": 6,
    "max_hours": 72,
    "bots": [],  # bots whose top-level comments count as feedback (their inline threads always do)
}
FAILED_CONCLUSIONS = {"FAILURE", "TIMED_OUT", "STARTUP_FAILURE", "ACTION_REQUIRED", "CANCELLED"}
PASSED_CONCLUSIONS = {"SUCCESS", "NEUTRAL", "SKIPPED"}
MERGEABLE_STATES = {"CLEAN", "UNSTABLE", "HAS_HOOKS"}
LOG_TAIL_LINES = 150
# A failed check whose log shows the runner or the network failing, not the code: re-run it once.
INFRA_PATTERNS = re.compile(
    r"runner has received a shutdown signal|lost communication with the server|no space left on device"
    r"|ETIMEDOUT|ECONNRESET|connection reset by peer|could not resolve host|temporary failure in name resolution"
    r"|\b50[234] (?:bad gateway|service unavailable|gateway time-?out)|read timed out|connect timed out"
    r"|gradle (?:build )?daemon disappeared unexpectedly|could not (?:get|head|download) '?https?://"
    r"|api rate limit exceeded|the operation was canceled|the job was not acquired by runner",
    re.I,
)
INFRA_CONCLUSIONS = {"TIMED_OUT", "STARTUP_FAILURE"}  # never CANCELLED: a person may have cancelled it
# A compiler, lint or test line that names a file: only these lines can blame this PR's code.
ERROR_LINE = re.compile(r"\s*(?:e:|w:|error:|\S+:\d+:\s*(?:error|warning)|.*\bFAILED\b)", re.I)
SOURCE_SUFFIXES = (".kt", ".kts", ".java", ".xml")

QUERY = """
query($owner: String!, $repo: String!, $number: Int!) {
  viewer { login }
  repository(owner: $owner, name: $repo) {
    squashMergeAllowed mergeCommitAllowed rebaseMergeAllowed
    pullRequest(number: $number) {
      id number url title state isDraft mergeable mergeStateStatus reviewDecision createdAt
      headRefName headRefOid baseRefName author { login }
      reviewRequests(first: 50) { nodes { requestedReviewer {
        __typename ... on User { login } ... on Team { slug } ... on Bot { login } } } }
      latestOpinionatedReviews(first: 50) { nodes { author { __typename login } state submittedAt } }
      reviews(last: 50) { nodes { id author { __typename login } state body submittedAt url } }
      reviewThreads(first: 100) { nodes { id isResolved isOutdated path line
        comments(last: 30) { nodes { id author { __typename login } body createdAt url } } } }
      comments(last: 100) { nodes { id author { __typename login } body createdAt url } }
      commits(last: 1) { nodes { commit { oid committedDate statusCheckRollup { state contexts(first: 100) { nodes {
        __typename
        ... on CheckRun { name status conclusion detailsUrl completedAt isRequired(pullRequestNumber: $number) }
        ... on StatusContext { context state targetUrl isRequired(pullRequestNumber: $number) } } } } } } }
    }
  }
}
"""


# --- GitHub access -------------------------------------------------------------------------------


def _remote(target: Path) -> tuple[str, str]:
    """(host, "owner/name") of `origin`."""
    url = subprocess.run(
        ["git", "-C", str(target), "remote", "get-url", "origin"], text=True, capture_output=True, check=False,
    ).stdout.strip()
    match = re.match(r"(?:git@|ssh://git@|https?://)([^:/]+)[:/](.+?)(?:\.git)?/?$", url)
    if not match:
        raise ValueError(f"cannot read the repository from the `origin` remote ({url or 'none'})")
    host, repo = match.groups()
    if "github" not in host:
        raise ValueError(f"the PR Watcher supports GitHub only; `origin` is {host}")
    return host, repo


def _gh(args: list[str], env: dict[str, str] | None = None, stdin: str | None = None, timeout: int = 120,
        cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["gh", *args], text=True, capture_output=True, input=stdin, timeout=timeout, check=False,
            env={**os.environ, **(env or {}), "GH_PROMPT_DISABLED": "1"}, cwd=cwd,
        )
    except FileNotFoundError as error:
        raise ValueError("`gh` is not installed; the PR Watcher needs the GitHub CLI, logged in") from error
    except subprocess.TimeoutExpired as error:
        raise ValueError(f"`gh {' '.join(args[:2])}` timed out after {timeout}s") from error


def _accounts(host: str) -> list[str]:
    status = _gh(["auth", "status", "--hostname", host])
    return re.findall(r"account (\S+)", status.stdout + status.stderr)


def github_env(target: Path, prefer: str | None = None) -> dict[str, str]:
    """The `gh` environment for this repository: the logged-in account that can see it.

    `gh` keeps one active account; a developer with a work and a personal account would otherwise
    switch by hand before every PR. The account that worked is remembered per repository. `prefer`
    (the PR's author) wins when it is logged in: the watcher speaks as the person who opened the PR.
    """
    if os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN"):
        return {}
    host, repo = _remote(target)
    cache = cache_dir(target) / "github-account.json"
    try:
        remembered = json.loads(cache.read_text(encoding="utf-8")).get(repo)
    except (OSError, ValueError):
        remembered = None
    accounts = _accounts(host)
    candidates = [None, *accounts]
    if remembered:
        candidates.insert(0, remembered)
    if prefer and prefer in accounts:
        candidates.insert(0, prefer)
    for login in dict.fromkeys(candidates):
        if login is None and prefer and prefer in accounts:
            continue  # the active account is someone else: never speak as them on this PR
        env: dict[str, str] = {}
        if login:
            token = _gh(["auth", "token", "--hostname", host, "--user", login])
            if token.returncode != 0 or not token.stdout.strip():
                continue
            env = {"GH_TOKEN": token.stdout.strip()}
        if _gh(["api", f"repos/{repo}", "--jq", ".full_name"], env=env).returncode == 0:
            if login and login != remembered:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(json.dumps({repo: login}), encoding="utf-8")
            return env
    raise ValueError(f"no logged-in `gh` account can see {repo}; run `gh auth login`")


def _graphql(env: dict[str, str], query: str, **variables: Any) -> dict[str, Any]:
    args = ["api", "graphql", "-f", f"query={query}"]
    for key, value in variables.items():
        args += ["-F" if isinstance(value, int) else "-f", f"{key}={value}"]
    result = _gh(args, env=env)
    if result.returncode != 0:
        raise ValueError(f"GitHub query failed: {(result.stderr or result.stdout).strip()[-600:]}")
    data = json.loads(result.stdout)
    if data.get("errors"):
        raise ValueError(f"GitHub query failed: {data['errors'][0].get('message')}")
    return data["data"]


# --- Watch state ---------------------------------------------------------------------------------


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def load_watch(agent_dir: Path) -> dict[str, Any]:
    watch = _read(agent_dir / WATCH_FILE)
    watch.setdefault("posted", [])
    watch.setdefault("handled", {})
    watch.setdefault("ci", {})
    watch.setdefault("rounds", 0)
    return watch


def watch_config(target: Path) -> dict[str, Any]:
    overrides = _read(target / ".ai" / "android-workflow.json").get("watch") or {}
    return {**DEFAULT_WATCH, **{key: value for key, value in overrides.items() if key in DEFAULT_WATCH}}


def pr_number(target: Path, agent_dir: Path, watch: dict[str, Any], env: dict[str, str], given: int | None) -> int:
    if given:
        return given
    if watch.get("pr", {}).get("number"):
        return int(watch["pr"]["number"])
    url = ((_read(agent_dir / "delivery.json").get("pr")) or {}).get("url") or ""
    found = re.search(r"/pull/(\d+)", url)
    if found:
        return int(found.group(1))
    branch = subprocess.run(
        ["git", "-C", str(target), "rev-parse", "--abbrev-ref", "HEAD"], text=True, capture_output=True, check=False,
    ).stdout.strip()
    view = _gh(["pr", "view", branch, "--json", "number", "--jq", ".number"], env=env, cwd=target)
    if view.returncode == 0 and view.stdout.strip().isdigit():
        return int(view.stdout.strip())
    raise ValueError("no PR for this run: open it first (`CLI deliver`) or pass --pr NUMBER")


# --- Reading the PR ------------------------------------------------------------------------------


def _login(node: dict[str, Any] | None) -> str:
    return str(((node or {}).get("author") or {}).get("login") or "ghost")


def _is_bot(node: dict[str, Any]) -> bool:
    author = node.get("author") or {}
    return author.get("__typename") == "Bot" or str(author.get("login") or "").endswith("[bot]")


def _epoch(stamp: str | None) -> float | None:
    if not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _check(node: dict[str, Any]) -> dict[str, Any]:
    if node.get("__typename") == "StatusContext":
        state = str(node.get("state") or "")
        outcome = "passed" if state == "SUCCESS" else "failed" if state in {"FAILURE", "ERROR"} else "pending"
        return {"name": node.get("context"), "outcome": outcome, "conclusion": state,
                "url": node.get("targetUrl"), "required": bool(node.get("isRequired"))}
    status, conclusion = str(node.get("status") or ""), str(node.get("conclusion") or "")
    if status != "COMPLETED":
        outcome = "pending"
    else:
        outcome = "passed" if conclusion in PASSED_CONCLUSIONS else "failed" if conclusion in FAILED_CONCLUSIONS else "pending"
    return {"name": node.get("name"), "outcome": outcome, "conclusion": conclusion or status,
            "url": node.get("detailsUrl"), "required": bool(node.get("isRequired")), "completed": node.get("completedAt")}


def assess(pr: dict[str, Any], viewer: str, watch: dict[str, Any], config: dict[str, Any], now: float) -> dict[str, Any]:
    """The next action for this PR. Pure: the same PR and watch state always give the same answer."""
    number = pr.get("number")
    if pr.get("state") == "MERGED":
        return {"action": "merged", "pr": number}
    if pr.get("state") == "CLOSED":
        return {"action": "closed", "pr": number}
    posted, handled = set(watch["posted"]), set(watch["handled"])
    bots = {str(name).lower() for name in config.get("bots") or []}
    items: list[dict[str, Any]] = []
    answered: list[str] = []

    for thread in (pr.get("reviewThreads") or {}).get("nodes") or []:
        comments = (thread.get("comments") or {}).get("nodes") or []
        if thread.get("isResolved") or not comments:
            continue
        last = comments[-1]
        if last["id"] in posted:
            answered.append(thread["id"])  # waiting for the reviewer: never resolved by us
            continue
        if last["id"] in handled:
            continue
        items.append({
            "id": last["id"], "kind": "thread", "thread": thread["id"], "author": _login(last), "bot": _is_bot(last),
            "own": _login(last) == viewer,
            "path": thread.get("path"), "line": thread.get("line"), "outdated": bool(thread.get("isOutdated")),
            "url": last.get("url"), "body": last.get("body") or "",
            "conversation": [{"author": _login(c), "body": c.get("body") or "", "ours": c["id"] in posted}
                             for c in comments[:-1]],
        })

    def top_level(node: dict[str, Any], kind: str) -> None:
        if node["id"] in posted or node["id"] in handled or not (node.get("body") or "").strip():
            return
        if _is_bot(node) and _login(node).lower().removesuffix("[bot]") not in bots and _login(node).lower() not in bots:
            return  # summaries, coverage reports: the checks already say what they found
        items.append({"id": node["id"], "kind": kind, "author": _login(node), "bot": _is_bot(node),
                      "own": _login(node) == viewer, "url": node.get("url"), "body": node.get("body") or "",
                      **({"state": node.get("state")} if kind == "review" else {})})

    for comment in (pr.get("comments") or {}).get("nodes") or []:
        top_level(comment, "comment")
    for review in (pr.get("reviews") or {}).get("nodes") or []:
        if review.get("state") in {"COMMENTED", "CHANGES_REQUESTED"}:
            top_level(review, "review")

    commit = (((pr.get("commits") or {}).get("nodes") or [{}])[0].get("commit")) or {}
    head = pr.get("headRefOid") or commit.get("oid")
    checks = [_check(node) for node in (((commit.get("statusCheckRollup") or {}).get("contexts") or {}).get("nodes") or [])]
    failing = [item for item in checks if item["outcome"] == "failed"]
    def seen(item: dict[str, Any]) -> bool:
        key = f"ci:{head[:12]}:{item['name']}"
        if watch["ci"].get(key) == "rerun":  # failed again after its re-run: back to the agent
            rerun_at = (watch.get("ci_at") or {}).get(key) or 0
            return not (_epoch(item.get("completed")) or 0) > rerun_at
        return key in watch["ci"]

    ci_items = [{**item, "id": f"ci:{head[:12]}:{item['name']}"} for item in failing if not seen(item)]
    pushed = _epoch(commit.get("committedDate")) or now
    grace = float(config.get("optional_check_grace_minutes") or 0) * 60
    blocking_pending = [
        item["name"] for item in checks
        if item["outcome"] == "pending" and (item["required"] or now - pushed < grace)
    ]
    blocking_failed = [
        item["name"] for item in failing
        if item["required"] or watch["ci"].get(f"ci:{head[:12]}:{item['name']}") not in {"unrelated", "rerun"}
    ]

    requested = [
        (node.get("requestedReviewer") or {}).get("login") or "team:" + str((node.get("requestedReviewer") or {}).get("slug"))
        for node in (pr.get("reviewRequests") or {}).get("nodes") or []
    ]
    latest = (pr.get("latestOpinionatedReviews") or {}).get("nodes") or []
    humans = [node for node in latest if not _is_bot(node) and _login(node) != viewer]
    changes_requested = [_login(node) for node in humans if node.get("state") == "CHANGES_REQUESTED"]
    approved = [_login(node) for node in humans if node.get("state") == "APPROVED"]

    decision: dict[str, Any] = {
        "pr": number, "url": pr.get("url"), "head": head, "draft": bool(pr.get("isDraft")),
        "checks": {
            "total": len(checks), "passed": sum(item["outcome"] == "passed" for item in checks),
            "pending": [item["name"] for item in checks if item["outcome"] == "pending"],
            "failed": [item["name"] for item in failing],
        },
        "reviewers": {"requested": requested, "approved": approved, "changes_requested": changes_requested},
        "answered_threads": len(answered),
    }
    if watch["rounds"] >= int(config.get("max_rounds") or 0) and (items or ci_items):
        return {**decision, "action": "needs_user",
                "reason": f"{watch['rounds']} fix rounds already; the PR keeps getting new findings"}
    if items or ci_items:
        return {**decision, "action": "respond", "items": items, "ci": ci_items}

    merge_state = str(pr.get("mergeStateStatus") or "UNKNOWN")
    if pr.get("mergeable") == "CONFLICTING" or merge_state == "DIRTY":
        return {**decision, "action": "sync", "reason": "conflict"}
    if merge_state == "BEHIND":
        return {**decision, "action": "sync", "reason": "behind"}

    waiting: list[str] = []
    if pr.get("isDraft"):
        waiting.append("the PR is a draft: mark it ready for review to let it merge")
    if blocking_failed:
        waiting.append("failing checks: " + ", ".join(blocking_failed[:6]))
    if blocking_pending:
        waiting.append("checks running: " + ", ".join(blocking_pending[:6]))
    approved_pr = pr.get("reviewDecision") == "APPROVED" and bool(approved) and not changes_requested
    if requested and (config.get("wait_for_requested_reviewers") or not approved_pr):
        waiting.append("review from " + ", ".join(requested))
    rereview = [login for login in changes_requested if login not in requested]
    if rereview:
        waiting.append("re-review from " + ", ".join(rereview))
    if pr.get("reviewDecision") in {"REVIEW_REQUIRED", "CHANGES_REQUESTED"} and not requested and not changes_requested:
        waiting.append("the repository requires an approving review")
    if not approved and not requested and not changes_requested:
        age = now - (_epoch(pr.get("createdAt")) or now)
        minutes = float(config.get("unreviewed_merge_after_minutes") or 0)
        if config.get("require_review"):
            waiting.append("an approving review (watch.require_review)")
        elif age < minutes * 60:
            waiting.append(f"a reviewer may still be added (no reviewer yet; merging after {int(minutes)} min open)")
    if not waiting and merge_state not in MERGEABLE_STATES:
        waiting.append(f"GitHub does not allow the merge yet ({merge_state.lower()})")
    if waiting:
        return {**decision, "action": "wait", "waiting_for": waiting}
    return {**decision, "action": "merge", "auto_merge": bool(config.get("auto_merge"))}


def _ci_log(target: Path, agent_dir: Path, env: dict[str, str], item: dict[str, Any]) -> str | None:
    """The failed steps' log of a GitHub Actions job, saved for the agent; other CI only has its link."""
    found = re.search(r"/actions/runs/\d+/job/(\d+)", str(item.get("url") or ""))
    if not found:
        return None
    view = _gh(["run", "view", "--job", found.group(1), "--log-failed"], env=env, cwd=target, timeout=180)
    text = view.stdout if view.returncode == 0 else view.stderr
    lines = text.strip().splitlines()[-LOG_TAIL_LINES:]
    if not lines:
        return None
    path = agent_dir / CI_LOG_DIR / (re.sub(r"[^A-Za-z0-9._-]+", "-", str(item["name"])).strip("-") + ".log")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path.relative_to(agent_dir))


def classify_ci(item: dict[str, Any], log_text: str, touched: list[str]) -> dict[str, Any] | None:
    """What a failing check most likely is, from evidence only: `rerun` when the runner or the
    network failed, `ours` when its log names a file this PR changed. None: the agent reads it."""
    if str(item.get("conclusion") or "").upper() in INFRA_CONCLUSIONS:
        return {"verdict": "rerun", "evidence": f"conclusion {item.get('conclusion')}"}
    error_lines = "\n".join(line for line in log_text.splitlines() if ERROR_LINE.match(line))
    named = sorted({
        path for path in touched
        if path and (path in log_text or (path.endswith(SOURCE_SUFFIXES) and not path.endswith(".gradle.kts")
                                          and Path(path).name in error_lines))
    })
    if named:
        return {"verdict": "ours", "evidence": "its log names " + ", ".join(named[:3])}
    found = INFRA_PATTERNS.search(log_text)
    if found:
        return {"verdict": "rerun", "evidence": f"infrastructure: {found.group(0)}"}
    return None


def _touched(target: Path, base: str | None) -> list[str]:
    if not base:
        return []
    out = subprocess.run(["git", "-C", str(target), "diff", "--name-only", f"origin/{base}...HEAD"],
                         text=True, capture_output=True, check=False)
    return [line for line in out.stdout.splitlines() if line.strip()] if out.returncode == 0 else []


def _rerun(target: Path, env: dict[str, str], item: dict[str, Any]) -> str | None:
    """Re-run a GitHub Actions job's failed steps; the error text, or None when it was re-run."""
    found = re.search(r"/actions/runs/(\d+)", str(item.get("url") or ""))
    if not found:
        return "not a GitHub Actions run"
    rerun = _gh(["run", "rerun", found.group(1), "--failed"], env=env, cwd=target)
    return None if rerun.returncode == 0 else (rerun.stderr or rerun.stdout).strip()[-300:]


def _local(target: Path, head: str | None, branch: str | None) -> dict[str, Any]:
    def git(*args: str) -> str:
        return subprocess.run(["git", "-C", str(target), *args], text=True, capture_output=True, check=False).stdout.strip()

    local_head = git("rev-parse", "HEAD")
    dirty = [line[3:] for line in git("status", "--porcelain").splitlines() if not line[3:].startswith(".ai/")]
    return {"branch": git("rev-parse", "--abbrev-ref", "HEAD"), "on_pr_branch": git("rev-parse", "--abbrev-ref", "HEAD") == branch,
            "matches_pr": bool(head) and local_head == head, "uncommitted": dirty[:10]}


def status(target: Path, number: int | None = None, record: bool = True) -> dict[str, Any]:
    """Read the PR once and decide the next action. With `respond`, the inbox is written in full."""
    from android_workflow.cli import append_stage_log

    agent_dir = run_dir(target)
    if not (agent_dir / "run-state.json").is_file():
        raise ValueError("no run for this ticket here; `CLI start` it first, then watch its PR")
    watch = load_watch(agent_dir)
    env = _env(target, watch)
    _host, repo = _remote(target)
    number = pr_number(target, agent_dir, watch, env, number)
    owner, name = repo.split("/", 1)
    data = _graphql(env, QUERY, owner=owner, repo=name, number=number)
    pr = (data.get("repository") or {}).get("pullRequest")
    if not pr:
        raise ValueError(f"PR #{number} was not found in {repo}")
    author = _login(pr)
    if author != (data.get("viewer") or {}).get("login"):
        author_env = github_env(target, prefer=author)
        if author_env != env:  # read again as the PR's author, so "our" comments are theirs
            env = author_env
            data = _graphql(env, QUERY, owner=owner, repo=name, number=number)
            pr = data["repository"]["pullRequest"]
    now = time.time()
    if not watch.get("started_at"):
        watch["started_at"] = now
    watch["pr"] = {"number": number, "url": pr.get("url"), "repo": repo, "node": pr.get("id"), "author": _login(pr),
                   "head_branch": pr.get("headRefName"), "base": pr.get("baseRefName"), "title": pr.get("title")}
    watch["merge_methods"] = [method for method, allowed in (
        ("squash", data["repository"].get("squashMergeAllowed")), ("merge", data["repository"].get("mergeCommitAllowed")),
        ("rebase", data["repository"].get("rebaseMergeAllowed"))) if allowed]
    config = watch_config(target)
    decision = assess(pr, str((data.get("viewer") or {}).get("login") or ""), watch, config, now)
    if decision["action"] in {"wait", "respond", "sync", "merge"} and now - watch["started_at"] > float(config["max_hours"]) * 3600:
        decision = {**decision, "action": "needs_user", "reason": f"watched for {config['max_hours']} hours"}
    decision["local"] = _local(target, decision.get("head"), pr.get("headRefName"))
    auto: list[str] = []
    if decision["action"] == "respond":
        touched = _touched(target, pr.get("baseRefName")) if decision["ci"] else []
        for item in decision["ci"]:
            log = _ci_log(target, agent_dir, env, item)
            if log:
                item["log"] = log
            text = (agent_dir / log).read_text(encoding="utf-8", errors="replace") if log else ""
            hint = classify_ci(item, text, touched)
            if hint:
                item["hint"] = hint

        def run_id(item: dict[str, Any]) -> str | None:
            found = re.search(r"/actions/runs/(\d+)", str(item.get("url") or ""))
            return found.group(1) if found else None

        # `gh run rerun --failed` re-runs every failed job of the run: only when all of them are re-runs.
        mixed = {run_id(item) for item in decision["ci"] if (item.get("hint") or {}).get("verdict") != "rerun"}
        rerun_runs: dict[str, str | None] = {}
        kept_ci = []
        for item in decision["ci"]:
            hint = item.get("hint")
            already = any(key.endswith(":" + str(item["name"])) and value == "rerun" for key, value in watch["ci"].items())
            rid = run_id(item)
            if hint and hint["verdict"] == "rerun" and not already and record and rid and rid not in mixed:
                if rid not in rerun_runs:
                    rerun_runs[rid] = _rerun(target, env, item)
                error = rerun_runs[rid]
                if error is None:
                    # Decided by code: the agent never sees it unless it fails again after the re-run.
                    watch["ci"][item["id"]] = "rerun"
                    watch.setdefault("ci_at", {})[item["id"]] = round(now, 3)
                    watch.setdefault("ci_reasons", {})[item["id"]] = "auto: " + hint["evidence"]
                    auto.append(f"{item['name']} ({hint['evidence']})")
                    continue
                item["hint"] = {**hint, "rerun_error": error}
            kept_ci.append(item)
        decision["ci"] = kept_ci
        if auto:
            append_stage_log(agent_dir, "T10", "rerun", "cli", "re-ran: " + "; ".join(auto))
        if not decision["items"] and not decision["ci"]:
            decision = {**{key: value for key, value in decision.items() if key not in {"items", "ci"}},
                        "action": "wait", "waiting_for": ["re-run of " + ", ".join(name.split(" (")[0] for name in auto)]}
    if decision["action"] == "respond":
        _write(agent_dir / INBOX_FILE, {
            "pr": number, "url": pr.get("url"), "head": decision["head"], "base": pr.get("baseRefName"),
            "items": decision["items"], "ci": decision["ci"],
            "reviewers": decision["reviewers"], "written_at": round(now, 3),
        })
        decision = {**decision, "inbox": f"{agent_dir}/{INBOX_FILE}"}
    if record:
        summary = _summary(decision)
        last = watch.get("last") or {}
        if _log_key(decision, summary) != last.get("log_key", last.get("summary")):
            append_stage_log(agent_dir, "T10", decision["action"], "cli", summary)
        if summary != last.get("summary"):
            _mark_desk(target, decision["action"], summary)
        if decision["action"] == "respond":
            ids = sorted(item["id"] for item in [*decision["items"], *decision["ci"]])
            if ids != watch.get("inbox_ids"):  # a new batch of work, not the same one read again
                watch["rounds"] = int(watch["rounds"]) + 1
                watch["inbox_ids"] = ids
        watch["last"] = {"action": decision["action"], "summary": summary, "log_key": _log_key(decision, summary),
                         "at": round(now, 3)}
        if decision["action"] in {"merged", "closed"}:
            watch["ended"] = decision["action"]
        _write(agent_dir / WATCH_FILE, watch)
    return _compact(decision)


DESK_STATUS = {"wait": "watching", "sync": "watching", "merge": "watching", "merged": "completed",
               "closed": "skipped", "needs_user": "blocked"}


def _mark_desk(target: Path, action: str, summary: str) -> None:
    """The office shows the watcher waiting on the PR between rounds, and refreshes its page."""
    from android_workflow.cli import load_state, refresh_office, run_lock, save_state

    with run_lock(target):
        state = load_state(target)
        entry = state.setdefault("stages", {}).setdefault("T10", {})
        if entry.get("status") != "started" and action in DESK_STATUS:  # an agent at work keeps its desk lit
            entry.update({"status": DESK_STATUS[action], "note": summary})
            save_state(target, state)
    refresh_office(target)


def _waiting_kinds(decision: dict[str, Any]) -> list[str]:
    """What a `wait` waits for, without the names that change on every poll (which checks still run)."""
    return [reason.split(":", 1)[0] if reason.startswith(("checks running:", "failing checks:")) else reason
            for reason in decision.get("waiting_for") or []]


def _log_key(decision: dict[str, Any], summary: str) -> str:
    """A new chat-log line only when the kind of wait changes: CI moving from one check to the next is not news."""
    if decision["action"] == "wait":
        return "wait: " + "; ".join(_waiting_kinds(decision))
    return summary


def _summary(decision: dict[str, Any]) -> str:
    action = decision["action"]
    if action == "respond":
        parts = []
        if decision["items"]:
            parts.append(f"{len(decision['items'])} new comment(s)")
        if decision["ci"]:
            parts.append(f"{len(decision['ci'])} failing check(s)")
        return ", ".join(parts)
    if action == "wait":
        return "; ".join(decision.get("waiting_for") or [])[:300]
    if action in {"sync", "needs_user"}:
        return str(decision.get("reason") or "")
    if action == "merge":
        checks = decision["checks"]
        return f"ready: {checks['passed']}/{checks['total']} checks passed, approved by {', '.join(decision['reviewers']['approved']) or 'no reviewer requested'}"
    return f"PR #{decision.get('pr')} {action}"


def _compact(decision: dict[str, Any]) -> dict[str, Any]:
    """What the orchestrator reads: the action and its reason, never the comment bodies."""
    out = {key: decision[key] for key in ("action", "pr", "url", "reason", "waiting_for", "inbox", "auto_merge",
                                          "draft", "reviewers", "local") if key in decision}
    if decision["action"] == "respond":
        out["items"] = len(decision["items"])
        out["ci"] = [{"name": item["name"], "required": item["required"],
                      **({"hint": item["hint"]["verdict"]} if item.get("hint") else {})} for item in decision["ci"]]
    if "checks" in decision:
        checks = decision["checks"]
        out["checks"] = f"{checks['passed']}/{checks['total']} passed" + (
            f", {len(checks['pending'])} running" if checks["pending"] else "") + (
            f", failing: {', '.join(checks['failed'][:5])}" if checks["failed"] else "")
    return out


def wait(target: Path, number: int | None = None, timeout: float = 540, interval: float = 60) -> dict[str, Any]:
    """Poll until there is something to do (or `timeout`): every `interval`, slower while nothing changes."""
    deadline = time.monotonic() + max(timeout, 0)
    delay = max(interval, 15)
    last = None
    while True:
        result = status(target, number)
        if result["action"] != "wait":
            return result
        if time.monotonic() + delay > deadline:
            return {**result, "timed_out": True}
        summary = json.dumps(_waiting_kinds(result))
        delay = min(delay * 1.5, 300) if summary == last else max(interval, 15)
        last = summary
        time.sleep(delay)


# --- Acting on the PR ----------------------------------------------------------------------------


def _env(target: Path, watch: dict[str, Any]) -> dict[str, str]:
    """The account of the PR's author once known (replies, re-runs and the merge are theirs)."""
    return github_env(target, prefer=(watch.get("pr") or {}).get("author"))


def _strip(body: str) -> str:
    from android_workflow.cli import strip_agent_attribution

    text = strip_agent_attribution(body).strip()
    if not text:
        raise ValueError("the reply is empty")
    return text


def _find_item(agent_dir: Path, item_id: str) -> dict[str, Any]:
    inbox = _read(agent_dir / INBOX_FILE)
    for item in [*(inbox.get("items") or []), *(inbox.get("ci") or [])]:
        if item.get("id") == item_id:
            return item
    raise ValueError(f"{item_id} is not in {INBOX_FILE}; run `CLI pr status` first")


def reply(target: Path, item_id: str, body: str | None = None, skip: str | None = None) -> dict[str, Any]:
    """Answer one comment (a thread reply, or a PR comment that quotes it) or mark it handled with a reason."""
    agent_dir = run_dir(target)
    watch = load_watch(agent_dir)
    item = _find_item(agent_dir, item_id)
    if item.get("kind") not in {"thread", "comment", "review"}:
        raise ValueError(f"{item_id} is a check; use `CLI pr ci`")
    if skip is not None:
        watch["handled"][item_id] = {"how": "skipped", "reason": skip, "at": round(time.time(), 3)}
        _write(agent_dir / WATCH_FILE, watch)
        return {"item": item_id, "status": "skipped"}
    text = _strip(body or "")
    env = _env(target, watch)
    if item["kind"] == "thread":
        data = _graphql(
            env, "mutation($thread: ID!, $body: String!) { addPullRequestReviewThreadReply("
                 "input: {pullRequestReviewThreadId: $thread, body: $body}) { comment { id url } } }",
            thread=item["thread"], body=text,
        )
        comment = data["addPullRequestReviewThreadReply"]["comment"]
    else:
        quote = next((line for line in item["body"].splitlines() if line.strip()), "")[:160]
        text = f"> {quote}\n\n@{item['author']} {text}"
        data = _graphql(
            env, "mutation($subject: ID!, $body: String!) { addComment(input: {subjectId: $subject, body: $body}) "
                 "{ commentEdge { node { id url } } } }",
            subject=watch["pr"]["node"], body=text,
        )
        comment = data["addComment"]["commentEdge"]["node"]
    watch["posted"].append(comment["id"])
    watch["handled"][item_id] = {"how": "replied", "reply": comment["id"], "at": round(time.time(), 3)}
    _write(agent_dir / WATCH_FILE, watch)
    return {"item": item_id, "status": "replied", "url": comment.get("url")}


def rerequest(target: Path, logins: list[str]) -> dict[str, Any]:
    """Ask reviewers who requested changes to look again, once the fix is pushed."""
    agent_dir = run_dir(target)
    watch = load_watch(agent_dir)
    env = _env(target, watch)
    repo, number = watch["pr"]["repo"], watch["pr"]["number"]
    args = ["api", "-X", "POST", f"repos/{repo}/pulls/{number}/requested_reviewers"]
    for login in logins:
        args += ["-f", f"reviewers[]={login}"]
    result = _gh(args, env=env)
    if result.returncode != 0:
        raise ValueError(f"could not request the review: {(result.stderr or result.stdout).strip()[-300:]}")
    return {"rerequested": logins}


def ci(target: Path, check: str, verdict: str, reason: str = "") -> dict[str, Any]:
    """Record what a failing check is: `ours` (a fix round follows), `unrelated` (fails without this change)
    or `rerun` (flaky or infrastructure: run it again once)."""
    agent_dir = run_dir(target)
    watch = load_watch(agent_dir)
    item = next((entry for entry in (_read(agent_dir / INBOX_FILE).get("ci") or [])
                 if entry.get("name") == check or entry.get("id") == check), None)
    if not item:
        raise ValueError(f"{check} is not a failing check in {INBOX_FILE}")
    result: dict[str, Any] = {"check": item["name"], "verdict": verdict}
    if verdict == "rerun":
        if any(key.endswith(":" + item["name"]) and value == "rerun" for key, value in watch["ci"].items()):
            raise ValueError(f"{item['name']} was already re-run once; call it `ours` or `unrelated`")
        found = re.search(r"/actions/runs/(\d+)", str(item.get("url") or ""))
        if not found:
            raise ValueError(f"{item['name']} does not run on GitHub Actions; re-run it from {item.get('url')}")
        rerun = _gh(["run", "rerun", found.group(1), "--failed"], env=_env(target, watch), cwd=target)
        if rerun.returncode != 0:
            raise ValueError(f"could not re-run it: {(rerun.stderr or rerun.stdout).strip()[-300:]}")
    if verdict == "unrelated" and item.get("required"):
        result["note"] = "a required check still blocks the merge until it passes"
    watch["ci"][item["id"]] = verdict
    watch.setdefault("ci_at", {})[item["id"]] = round(time.time(), 3)
    if reason:
        watch.setdefault("ci_reasons", {})[item["id"]] = reason
    _write(agent_dir / WATCH_FILE, watch)
    return result


def _git(target: Path, *args: str, timeout: int = 900) -> subprocess.CompletedProcess[str]:
    from android_workflow.delivery import git

    return git(target, *args, timeout=timeout)


def sync(target: Path, resume: bool = False) -> tuple[dict[str, Any], int]:
    """Bring the PR's own new commits and then the base branch into the local branch, and push.

    Never rebases or force-pushes: the base comes in as a merge commit. A conflict leaves the merge open
    with the files listed; after they are resolved (and the gate passed), `--continue` commits and pushes.
    """
    agent_dir = run_dir(target)
    watch = load_watch(agent_dir)
    pr = watch.get("pr") or {}
    if not pr.get("base"):
        raise ValueError("run `CLI pr status` first")
    branch = _git(target, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if branch != pr.get("head_branch"):
        raise ValueError(f"the checkout is on `{branch}`, not the PR branch `{pr.get('head_branch')}`")
    gitdir = Path(_git(target, "rev-parse", "--absolute-git-dir").stdout.strip())
    merging = (gitdir / "MERGE_HEAD").exists()
    if resume:
        if not merging:
            raise ValueError("no merge in progress")
        conflicted = _git(target, "diff", "--name-only", "--diff-filter=U").stdout.split()
        markers = [path for path in conflicted if re.search(r"^(<{7}|>{7}) ", (target / path).read_text(errors="ignore"), re.M)]
        if markers:
            return {"status": "conflict", "files": markers, "reason": "conflict markers are still in these files"}, 1
        if conflicted:
            _git(target, "add", "--", *conflicted)
        committed = _git(target, "commit", "--no-edit")
        if committed.returncode != 0:
            raise ValueError(f"git commit failed (hooks run here): {committed.stdout[-800:]}{committed.stderr[-800:]}")
    else:
        if merging:
            raise ValueError("a merge is already in progress: resolve it, then `CLI pr sync --continue`")
        dirty = [line for line in _git(target, "status", "--porcelain").stdout.splitlines() if not line[3:].startswith(".ai/")]
        if dirty:
            raise ValueError("uncommitted changes in the checkout; deliver or stash them before syncing")
        fetched = _git(target, "fetch", "origin", pr["base"], pr["head_branch"])
        if fetched.returncode != 0:
            raise ValueError(f"git fetch failed: {fetched.stderr[-400:]}")
        forward = _git(target, "merge", "--ff-only", f"origin/{pr['head_branch']}")
        if forward.returncode != 0:
            raise ValueError("the local branch and the PR branch have both moved; reconcile them by hand")
        merged = _git(target, "merge", "--no-edit", f"origin/{pr['base']}")
        if merged.returncode != 0:
            conflicted = _git(target, "diff", "--name-only", "--diff-filter=U").stdout.split()
            if not conflicted:
                _git(target, "merge", "--abort")
                raise ValueError(f"git merge failed: {(merged.stdout + merged.stderr)[-600:]}")
            return {"status": "conflict", "files": conflicted, "base": pr["base"]}, 1
    ahead = int(_git(target, "rev-list", "--count", f"origin/{pr['head_branch']}..HEAD").stdout.strip() or 0)
    if not ahead:
        return {"status": "up_to_date"}, 0
    pushed = _git(target, "push", "origin", f"HEAD:{pr['head_branch']}")
    if pushed.returncode != 0:
        raise ValueError(f"push failed (never force-pushed): {(pushed.stdout + pushed.stderr)[-800:]}")
    return {"status": "synced", "sha": _git(target, "rev-parse", "HEAD").stdout.strip()}, 0


def merge(target: Path, number: int | None = None) -> tuple[dict[str, Any], int]:
    """Merge the PR when, read again right now, it is ready; the head commit must be the one checked."""
    from android_workflow.cli import append_stage_log

    decision = status(target, number)
    if decision["action"] == "merged":
        return {"status": "merged", "pr": decision["pr"], "url": decision.get("url")}, 0
    if decision["action"] != "merge":
        return {"status": "not_ready", "action": decision["action"],
                "waiting_for": decision.get("waiting_for") or decision.get("reason")}, 1
    local = decision.get("local") or {}
    if local.get("uncommitted"):
        return {"status": "not_ready", "reason": "uncommitted local changes would be left out"}, 1
    agent_dir = run_dir(target)
    watch = load_watch(agent_dir)
    config = watch_config(target)
    methods = watch.get("merge_methods") or ["merge"]
    method = config.get("merge_method") if config.get("merge_method") in methods else methods[0]
    env = _env(target, watch)
    head = _graphql(env, "query($owner: String!, $repo: String!, $number: Int!) { repository(owner: $owner, "
                    "name: $repo) { pullRequest(number: $number) { headRefOid } } }",
                    owner=watch["pr"]["repo"].split("/")[0], repo=watch["pr"]["repo"].split("/")[1],
                    number=int(watch["pr"]["number"]))["repository"]["pullRequest"]["headRefOid"]
    result = _gh(["pr", "merge", str(watch["pr"]["number"]), f"--{method}", "--match-head-commit", head],
                 env=env, cwd=target, timeout=300)
    if result.returncode != 0:
        return {"status": "refused", "reason": (result.stderr or result.stdout).strip()[-600:]}, 1
    append_stage_log(agent_dir, "T10", "completed", "cli", f"PR #{watch['pr']['number']} merged ({method})")
    watch["ended"] = "merged"
    watch["merged"] = {"method": method, "head": head, "at": round(time.time(), 3)}
    _write(agent_dir / WATCH_FILE, watch)
    return {"status": "merged", "pr": watch["pr"]["number"], "url": watch["pr"]["url"], "method": method}, 0


def dispatch(target: Path, args: Any) -> tuple[dict[str, Any], int]:
    if args.action == "status":
        return status(target, args.pr), 0
    if args.action == "wait":
        return wait(target, args.pr, timeout=args.timeout, interval=args.interval), 0
    if args.action == "reply":
        if not args.item or (args.body is None) == (args.skip is None):
            raise ValueError("reply needs --item and either --body or --skip")
        return reply(target, args.item, body=args.body, skip=args.skip), 0
    if args.action == "rerequest":
        if not args.reviewer:
            raise ValueError("rerequest needs --reviewer LOGIN")
        return rerequest(target, args.reviewer), 0
    if args.action == "ci":
        if not args.check or args.verdict not in {"ours", "unrelated", "rerun"}:
            raise ValueError("ci needs --check NAME and --as ours|unrelated|rerun")
        return ci(target, args.check, args.verdict, args.reason or ""), 0
    if args.action == "sync":
        return sync(target, resume=args.resume)
    if args.action == "merge":
        return merge(target, args.pr)
    raise ValueError(f"unknown pr action: {args.action}")
