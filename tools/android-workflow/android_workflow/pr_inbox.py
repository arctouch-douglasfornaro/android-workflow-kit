"""PR Watcher inbox: listen for the developer's own open PRs and keep one watch session per PR until it merges.

`/android-workflow` (or `/android-workflow --watch`) with no ticket runs a listener: `CLI pr inbox` lists
the open PRs the developer authored in this app's repository and gives each one its own agent session
in a new terminal tab running the ordinary `/android-workflow <ID> --watch --pr N` (`CLI pr launch --pr N`).
That session answers comments, fixes CI, merges when approved and ends itself with `CLI pr finish`.

The session runs in the checkout of the PR's branch: the main checkout when it is on that branch, else
the worktree that has it, else a new worktree `../wt-<branch without the user prefix>` created from
`origin/<branch>`. A PR whose checkout already has a live watch (a launched tab, or a session that wrote
its run files in the last 15 minutes) is left alone, so a PR opened by a running `/android-workflow`
is never watched twice.

The first pass (`--once`, when the developer starts the listener) launches every PR with no live watch;
later passes (`--wait`) launch only PRs not launched before, so closing a tab stops that PR's watch until
the developer starts the listener again or runs `/android-workflow <ID> --watch`.

State lives in `MAIN/.ai/workflow/_cache/pr-inbox.json`. Terminal and agent settings are the
`review.inbox` ones (`terminal`, `command`, `agent`, `agent_command`).
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from android_workflow.paths import cache_dir

STATE_FILE = "pr-inbox.json"
DEFAULT_CONFIG = {"drafts": True}  # watch.inbox: draft PRs are watched too (comments still come)
TICKET_RE = re.compile(r"\b([A-Za-z][A-Za-z0-9]+-\d+)\b")

SEARCH_QUERY = """
query($q: String!) {
  viewer { login }
  search(query: $q, type: ISSUE, first: 50) {
    nodes { ... on PullRequest {
      number title url isDraft isCrossRepository headRefName headRefOid
    } }
  }
}
"""


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(cwd), *args], text=True, capture_output=True, check=False, timeout=600)


def config(main: Path) -> dict[str, Any]:
    watch = _read(main / ".ai" / "android-workflow.json").get("watch") or {}
    overrides = watch.get("inbox") if isinstance(watch.get("inbox"), dict) else {}
    return {**DEFAULT_CONFIG, **{key: value for key, value in overrides.items() if key in DEFAULT_CONFIG}}


def search(env: dict[str, str], repo: str) -> dict[int, dict[str, Any]]:
    from android_workflow.watch import _graphql

    data = _graphql(env, SEARCH_QUERY, q=f"is:pr is:open archived:false repo:{repo} author:@me")
    return {int(node["number"]): node for node in ((data.get("search") or {}).get("nodes")) or []
            if node and node.get("number")}


def skip_reason(pr: dict[str, Any], settings: dict[str, Any]) -> str | None:
    if pr.get("isCrossRepository"):
        return "opened from a fork: its branch is not in this repository"
    if pr.get("isDraft") and not settings["drafts"]:
        return "draft (watch.inbox.drafts is false)"
    if not pr.get("headRefName"):
        return "no head branch"
    return None


# --- the checkout of a PR ------------------------------------------------------------------------


def checkout_for(main: Path, branch: str) -> Path | None:
    """The checkout that has this branch: the main one, else a worktree (never a review or base checkout)."""
    from android_workflow.cleanup import worktrees

    if _git(main, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == branch:
        return main.resolve()
    for tree in worktrees(main):
        if tree.get("branch") == branch:
            return Path(tree["path"])
    return None


def worktree_path(main: Path, branch: str) -> Path:
    name = branch.split("/", 1)[1] if "/" in branch else branch
    return main.resolve().parent / ("wt-" + re.sub(r"[^A-Za-z0-9._-]+", "-", name))


def add_worktree(main: Path, branch: str) -> tuple[Path, dict[str, Any]]:
    """A worktree at `origin/<branch>` (the local branch when one exists, fast-forwarded when behind)."""
    from android_workflow.review import _load_workspace

    path = worktree_path(main, branch)
    if path.exists():
        raise ValueError(f"{path} exists and is not a worktree of {branch}: move it, or check out {branch} there")
    fetched = _git(main, "fetch", "origin", branch)
    if fetched.returncode != 0:
        raise ValueError(f"git fetch origin {branch} failed: {fetched.stderr.strip()[-300:]}")
    local = _git(main, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}").returncode == 0
    added = (_git(main, "worktree", "add", str(path), branch) if local
             else _git(main, "worktree", "add", "--track", "-b", branch, str(path), f"origin/{branch}"))
    if added.returncode != 0:
        raise ValueError(f"git worktree add failed: {added.stderr.strip()[-400:]}")
    workspace = _load_workspace()
    report: dict[str, Any] = {"created": str(path)}
    if local:
        report["sync"] = workspace.sync_with_remote(branch, str(path))
    try:
        report["bootstrap"] = workspace.bootstrap_worktree(str(main), str(path))
    except Exception as error:  # noqa: BLE001 - reported, the developer decides
        report["bootstrap"] = {"error": str(error)[:300]}
    return path, report


def ticket_for(checkout: Path | None, pr: dict[str, Any]) -> tuple[str, bool]:
    """(ticket id, the checkout already has its run). The run of this checkout when it is this PR's, else
    the key in the branch or title, else `PR-<n>`."""
    from android_workflow.paths import current_ticket_id, run_dir

    if checkout is not None:
        ticket = current_ticket_id(checkout)
        if ticket and (run_dir(checkout, ticket) / "run-state.json").is_file():
            number = (_read(run_dir(checkout, ticket) / "pr-watch.json").get("pr") or {}).get("number")
            if number in (None, pr["number"]):
                return ticket, True
    for text in (str(pr.get("headRefName") or "").split("/", 1)[-1], str(pr.get("title") or "")):
        match = TICKET_RE.search(text)
        if match:
            return match.group(1).upper(), False
    return f"PR-{pr['number']}", False


def watched(main: Path, checkout: Path | None, number: int) -> str | None:
    """Why this PR already has a watch (a launched tab alive, or a session working in its checkout), or None."""
    from android_workflow.cleanup import in_use
    from android_workflow.review_inbox import session_alive

    if checkout is None:
        return None
    if session_alive(checkout, number, "WATCH"):
        return "its watch tab is running"
    return in_use(main, checkout, {"number": number})


# --- the inbox -----------------------------------------------------------------------------------


def scan(main: Path, prs: dict[int, dict[str, Any]], settings: dict[str, Any], first: bool,
         now: float | None = None) -> dict[str, Any]:
    """What to launch, skip or leave. `first`: the developer just started the listener, so every PR with
    no live watch is launched again; later passes launch only PRs never launched."""
    now = time.time() if now is None else now
    path = cache_dir(main) / STATE_FILE
    state = _read(path)
    known: dict[str, dict[str, Any]] = state.setdefault("prs", {})
    launch: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    running: list[dict[str, Any]] = []
    for number in sorted(prs):
        pr = prs[number]
        entry = known.setdefault(str(number), {"first_seen": now})
        entry.update({"present": True, "title": pr.get("title"), "url": pr.get("url"), "branch": pr.get("headRefName")})
        reason = skip_reason(pr, settings)
        if reason:
            if entry.get("skipped") != reason:
                skipped.append({"pr": number, "title": pr.get("title"), "url": pr.get("url"), "why": reason})
            entry["skipped"] = reason
            continue
        entry.pop("skipped", None)
        checkout = checkout_for(main, pr["headRefName"])
        busy = watched(main, checkout, number)
        if busy:
            entry["watched"] = True
            running.append({"pr": number, "why": busy})
            continue
        if first or not (entry.get("launched_at") or entry.get("watched")):
            launch.append({"pr": number, "title": pr.get("title"), "url": pr.get("url"),
                           "branch": pr["headRefName"], "checkout": str(checkout) if checkout else None})
    for number, entry in known.items():
        if int(number) not in prs and entry.get("present"):
            entry.update({"present": False, "left_at": now})
    state["last_poll"] = now
    _write(path, state)
    return {"launch": launch, "skipped": skipped, "running": running}


def inbox(main: Path, wait: bool = False, timeout: float = 540, interval: float = 60,
          first: bool = False) -> dict[str, Any]:
    from android_workflow.watch import _remote, github_env, poll

    main = main.resolve()
    _, repo = _remote(main)
    settings = config(main)
    resolved: dict[str, dict[str, str]] = {}

    def step() -> tuple[bool, dict[str, Any], str]:
        if "env" not in resolved:
            resolved["env"] = github_env(main)
        found = scan(main, search(resolved["env"], repo), settings, first)
        return bool(found["launch"] or found["skipped"]), found, json.dumps([r["pr"] for r in found["running"]])

    if wait:
        result, timed_out = poll(step, timeout, interval)
    else:
        _, result, _ = step()
        timed_out = False
    if result.get("status") == "offline":
        return {"status": "offline", "repo": repo, "error": (result.get("waiting_for") or [""])[0],
                "next": "GitHub is unreachable (network, not a login problem): CLI pr inbox --wait again"}
    commands = [f"CLI pr launch --target {main} --pr {item['pr']}" for item in result["launch"]]
    status = "new" if commands else "skipped" if result["skipped"] else "timed_out" if timed_out else "idle"
    nxt = ("run each launch command, tell the developer one line per PR, then CLI pr inbox --wait again" if commands
           else "tell the developer which PRs are not watched and why, then CLI pr inbox --wait again"
           if result["skipped"] else "CLI pr inbox --wait again")
    return {"status": status, "repo": repo, **result, "commands": commands, "next": nxt}


def launch(main: Path, number: int, dry_run: bool = False) -> dict[str, Any]:
    """Start `/android-workflow <ID> --watch --pr N` for one of the developer's PRs in its branch's checkout."""
    from android_workflow.review_inbox import open_watch
    from android_workflow.watch import _graphql, _remote, github_env

    main = main.resolve()
    _, repo = _remote(main)
    owner, name = repo.split("/", 1)
    data = _graphql(github_env(main), "query($owner: String!, $name: String!, $number: Int!) { repository(owner: $owner, "
                    "name: $name) { pullRequest(number: $number) { number title url state isCrossRepository "
                    "headRefName } } }", owner=owner, name=name, number=number)
    pr = (data.get("repository") or {}).get("pullRequest")
    if not pr:
        raise ValueError(f"PR #{number} was not found in {repo}")
    if pr.get("state") != "OPEN":
        return {"status": "ended", "pr": number, "next": f"nothing: the PR is {str(pr.get('state')).lower()}"}
    if pr.get("isCrossRepository"):
        raise ValueError(f"PR #{number} comes from a fork: its branch cannot be checked out here")
    checkout = checkout_for(main, pr["headRefName"])
    busy = watched(main, checkout, number)
    if busy:
        return {"status": "running", "pr": number, "checkout": str(checkout), "next": f"nothing: {busy}"}
    created: dict[str, Any] = {}
    if checkout is None:
        if dry_run:
            checkout = worktree_path(main, pr["headRefName"])
            created = {"would_create": str(checkout)}
        else:
            checkout, created = add_worktree(main, pr["headRefName"])
    ticket, has_run = ticket_for(checkout, pr)
    result = open_watch(checkout, number, ticket, title=None if has_run else pr.get("title"), dry_run=dry_run)
    result.update({"checkout": str(checkout), "ticket": ticket, **({"worktree": created} if created else {})})
    if result["status"] == "launched":
        path = cache_dir(main) / STATE_FILE
        state = _read(path)
        entry = state.setdefault("prs", {}).setdefault(str(number), {"first_seen": time.time()})
        entry.update({"launched_at": time.time(), "launches": int(entry.get("launches") or 0) + 1,
                      "checkout": str(checkout), "ticket": ticket})
        _write(path, state)
    return result


def inbox_once(main: Path) -> dict[str, Any]:
    """The listener's first pass: every PR of the developer with no live watch gets its tab now."""
    found = inbox(main, wait=False, first=True)
    if found.get("status") == "offline":
        return found
    launched = []
    for item in found.get("launch") or []:
        try:
            result = launch(main, int(item["pr"]))
        except ValueError as error:
            result = {"status": "failed", "error": str(error)[:400]}
        launched.append({"pr": item["pr"], "status": result.get("status"), "tell": result.get("tell"),
                         **({"error": result["error"]} if result.get("error") else {})})
    lines = [entry["tell"] or f"PR #{entry['pr']}: {entry['status']}" + (f" ({entry['error']})" if entry.get("error") else "")
             for entry in launched]
    lines += [f"PR #{item['pr']}: already watched ({item['why']})" for item in found.get("running") or []]
    lines += [f"PR #{item['pr']} not watched: {item['why']}" for item in found.get("skipped") or []]
    return {"status": found.get("status"), "repo": found.get("repo"), "launched": launched,
            "running": found.get("running"), "skipped": found.get("skipped"),
            "summary": lines or ["you have no open PR in this repository right now"],
            "next": f"tell the developer the `summary` lines, then keep listening: CLI pr inbox --target {main} "
                    "--wait (background, --timeout 3300) and wait again after every result. Never end the listener "
                    "after one pass"}
