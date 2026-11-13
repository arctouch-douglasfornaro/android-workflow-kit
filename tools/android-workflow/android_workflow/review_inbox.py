"""Android Review inbox: listen for PRs that ask for the developer's review and start one review per PR.

`/android-review` with no PR runs a listener: `CLI review inbox --wait` polls GitHub for open PRs in
this app's repository that request a review from the developer (directly, and optionally through a
team) or are assigned to them. Each new PR gets its own agent session in a new terminal tab
(`CLI review launch --pr N`), running the ordinary `/android-review N`, so the review itself is the
unchanged workflow: it stops at the draft and posts nothing without the developer's OK.

State lives in `TARGET/.ai/workflow/_cache/review-inbox.json`; each launched session writes its PID to
`_cache/review-launch/PR-<n>.pid` (the launcher script `exec`s the agent), so the listener knows
whether a PR still has a live session and never starts a second one on the same worktree.

Starting a review builds the PR's code on this machine, so only PRs from this repository (never a
fork) by authors with a trusted association (owner, member, collaborator) are launched; anything
else is reported once and left for the developer to start by hand.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from android_workflow.paths import cache_dir

STATE_FILE = "review-inbox.json"
LAUNCH_DIR = "review-launch"

DEFAULT_INBOX = {
    "team_requests": False,          # also PRs that ask a team the developer is in
    "assigned": True,                # also PRs assigned to the developer
    "drafts": False,                 # draft PRs wait until they are ready for review
    "author_associations": ["OWNER", "MEMBER", "COLLABORATOR"],
    "skip_authors": ["dependabot", "renovate", "github-actions"],
    "max_parallel": 2,               # sessions still reviewing (a session that only watches does not count)
    "terminal": "auto",              # auto | terminal | iterm | command
    "command": None,                 # terminal "command": a shell command with {script}, e.g. "open -a MyTerm {script}"
    "agent_command": 'claude -n {name} {prompt}',  # the agent session started in the tab
}

SEARCH_QUERY = """
query($q: String!) {
  viewer { login }
  search(query: $q, type: ISSUE, first: 50) {
    nodes { ... on PullRequest {
      number title url isDraft isCrossRepository authorAssociation headRefOid updatedAt
      author { __typename login }
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


def inbox_config(target: Path) -> dict[str, Any]:
    review = _read(target / ".ai" / "android-workflow.json").get("review") or {}
    overrides = review.get("inbox") if isinstance(review.get("inbox"), dict) else {}
    config = {**DEFAULT_INBOX, **{key: value for key, value in overrides.items() if key in DEFAULT_INBOX}}
    try:
        config["max_parallel"] = max(1, int(config["max_parallel"]))
    except (TypeError, ValueError):
        config["max_parallel"] = DEFAULT_INBOX["max_parallel"]
    config["author_associations"] = [str(a).upper() for a in config.get("author_associations") or []]
    config["skip_authors"] = [str(a).lower() for a in config.get("skip_authors") or []]
    return config


def queries(repo: str, config: dict[str, Any]) -> list[tuple[str, str]]:
    """(reason, search) pairs: who asked for this review."""
    scope = f"is:pr is:open archived:false repo:{repo} -author:@me"
    found = [("review_requested", f"{scope} user-review-requested:@me")]
    if config["team_requests"]:
        found.append(("team_review_requested", f"{scope} review-requested:@me"))
    if config["assigned"]:
        found.append(("assigned", f"{scope} assignee:@me"))
    return found


def search(env: dict[str, str], repo: str, config: dict[str, Any]) -> dict[int, dict[str, Any]]:
    from android_workflow.watch import _graphql

    prs: dict[int, dict[str, Any]] = {}
    for reason, query in queries(repo, config):
        data = _graphql(env, SEARCH_QUERY, q=query)
        for node in ((data.get("search") or {}).get("nodes")) or []:
            if not node or not node.get("number"):
                continue
            entry = prs.setdefault(int(node["number"]), {**node, "reasons": []})
            if reason not in entry["reasons"]:
                entry["reasons"].append(reason)
    return prs


def skip_reason(pr: dict[str, Any], config: dict[str, Any]) -> str | None:
    """Why this PR is not started automatically, or None. The developer can still `/android-review N`."""
    author = pr.get("author") or {}
    login = str(author.get("login") or "")
    if pr.get("isDraft") and not config["drafts"]:
        return "draft"
    if author.get("__typename") == "Bot" or login.lower().removesuffix("[bot]") in config["skip_authors"]:
        return f"author {login or 'unknown'} is skipped"
    if pr.get("isCrossRepository"):
        return "opened from a fork: its code is not built automatically"
    association = str(pr.get("authorAssociation") or "NONE").upper()
    if association not in config["author_associations"]:
        return f"author association {association}: not trusted to build automatically"
    return None


# --- live sessions -------------------------------------------------------------------------------


def launch_dir(target: Path) -> Path:
    return cache_dir(target) / LAUNCH_DIR


def pid_file(target: Path, number: int) -> Path:
    return launch_dir(target) / f"PR-{number}.pid"


def session_alive(target: Path, number: int) -> bool:
    """The session launched for this PR still runs (its PID is alive and is still that launcher)."""
    try:
        pid = int(pid_file(target, number).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    command = subprocess.run(["ps", "-p", str(pid), "-o", "command="], text=True, capture_output=True,
                             check=False).stdout
    return bool(command.strip())


def session_watching(target: Path, number: int) -> bool:
    """The review of this PR was posted and the session only follows the PR now (frees a parallel slot)."""
    from android_workflow.review import STATE_FILE as REVIEW_STATE, _status_run_dir

    run = _status_run_dir(target, number)
    return bool(run and (_read(run / REVIEW_STATE).get("watch") or {}).get("started_at"))


# --- the inbox -----------------------------------------------------------------------------------


def scan(target: Path, prs: dict[int, dict[str, Any]], config: dict[str, Any],
         now: float | None = None) -> dict[str, Any]:
    """Compare what GitHub lists now with the inbox state; return what to launch, queue, skip, or leave.

    A PR is launched the first time it is listed, and again only when it left the list (our review
    removed the request) and came back (re-requested) while no session of it is alive."""
    now = time.time() if now is None else now
    path = cache_dir(target) / STATE_FILE
    state = _read(path)
    known: dict[str, dict[str, Any]] = state.setdefault("prs", {})
    want: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    running: list[int] = []
    reviewing = 0
    for number in sorted(prs):
        pr = prs[number]
        entry = known.setdefault(str(number), {"first_seen": now})
        was_present = entry.get("present")
        entry.update({"present": True, "title": pr.get("title"), "url": pr.get("url"),
                      "author": (pr.get("author") or {}).get("login"), "reasons": pr.get("reasons") or [],
                      "head": pr.get("headRefOid")})
        reason = skip_reason(pr, config)
        if reason:
            if entry.get("skipped") != reason:
                skipped.append({"pr": number, "title": pr.get("title"), "url": pr.get("url"), "why": reason})
            entry["skipped"] = reason
            continue
        entry.pop("skipped", None)
        if session_alive(target, number):
            running.append(number)
            reviewing += 0 if session_watching(target, number) else 1
            continue
        if not entry.get("launched_at"):
            want.append({"pr": number, "why": "new", "title": pr.get("title"), "url": pr.get("url"),
                         "reasons": pr.get("reasons") or []})
        elif was_present is False or entry.get("relaunch"):
            entry["relaunch"] = True  # kept until a session starts, so a queued or failed launch is retried
            want.append({"pr": number, "why": "rerequested", "title": pr.get("title"), "url": pr.get("url"),
                         "reasons": pr.get("reasons") or []})
    for number, entry in known.items():
        if int(number) not in prs and entry.get("present"):
            entry["present"] = False
            entry["left_at"] = now
    slots = max(0, config["max_parallel"] - reviewing)
    launch, queued = want[:slots], want[slots:]
    state["last_poll"] = now
    _write(path, state)
    return {"launch": launch, "queued": queued, "skipped": skipped, "running": running}


def inbox(target: Path, wait: bool = False, timeout: float = 540, interval: float = 60) -> dict[str, Any]:
    """One look (or, with `wait`, poll until there is a PR to launch or a new one to report)."""
    from android_workflow.watch import _remote, github_env, poll

    target = target.resolve()
    _, repo = _remote(target)
    env = github_env(target)
    config = inbox_config(target)

    def step() -> tuple[bool, dict[str, Any], str]:
        found = scan(target, search(env, repo, config), config)
        news = bool(found["launch"] or found["skipped"])
        return news, found, json.dumps([found["running"], [q["pr"] for q in found["queued"]]])

    if wait:
        result, timed_out = poll(step, timeout, interval)
    else:
        _, result, _ = step()
        timed_out = False
    commands = [f"CLI review launch --target {target} --pr {item['pr']}" for item in result["launch"]]
    status = "new" if result["launch"] else "skipped" if result["skipped"] else "timed_out" if timed_out else "idle"
    if commands:
        nxt = "run each launch command, tell the developer one line per PR, then CLI review inbox --wait again"
    elif result["skipped"]:
        nxt = ("tell the developer which PRs were not started and why (/android-review N starts one by hand), "
               "then CLI review inbox --wait again")
    else:
        nxt = "CLI review inbox --wait again"
    return {"status": status, "repo": repo, **result, "commands": commands, "next": nxt}


# --- launching one session -----------------------------------------------------------------------


def _shell() -> str:
    shell = os.environ.get("SHELL") or ""
    return shell if shell and Path(shell).is_file() else "/bin/zsh" if Path("/bin/zsh").is_file() else "/bin/bash"


def agent_command(target: Path, number: int, config: dict[str, Any]) -> str:
    template = str(config.get("agent_command") or DEFAULT_INBOX["agent_command"])
    return template.format(
        name=shlex.quote(f"review PR-{number}"),
        prompt=shlex.quote(f"/android-review {number} --target {target}"),
        pr=number, target=shlex.quote(str(target)),
    )


def write_script(target: Path, number: int, config: dict[str, Any]) -> Path:
    """The launcher: records its PID (kept by `exec`) and replaces itself with the agent session."""
    script = launch_dir(target) / f"PR-{number}.sh"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(
        "\n".join([
            f"#!{_shell()}",
            f"# android-review inbox: review session for PR #{number}",
            f"cd {shlex.quote(str(target))} || exit 1",
            f"echo $$ > {shlex.quote(str(pid_file(target, number)))}",
            f"exec {agent_command(target, number, config)}",
            "",
        ]),
        encoding="utf-8",
    )
    script.chmod(0o700)
    return script


def _applescript_string(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def terminal_kind(config: dict[str, Any]) -> str:
    kind = str(config.get("terminal") or "auto").lower()
    if kind != "auto":
        return kind
    if config.get("command"):
        return "command"
    if sys.platform != "darwin":
        raise ValueError("no terminal to open on this OS: set review.inbox.command (a shell command with {script})")
    return "iterm" if os.environ.get("TERM_PROGRAM") == "iTerm.app" else "terminal"


def open_command(script: Path, config: dict[str, Any]) -> list[str]:
    run = f"{_shell()} -l {shlex.quote(str(script))}"
    kind = terminal_kind(config)
    if kind == "terminal":
        return ["osascript", "-e", f'tell application "Terminal" to do script {_applescript_string(run)}']
    if kind == "iterm":
        command = _applescript_string(run)
        return ["osascript",
                "-e", 'tell application "iTerm"',
                "-e", "if (count of windows) = 0 then",
                "-e", f"create window with default profile command {command}",
                "-e", "else",
                "-e", f"tell current window to create tab with default profile command {command}",
                "-e", "end if",
                "-e", "end tell"]
    if kind == "command":
        template = str(config.get("command") or "")
        if "{script}" not in template:
            raise ValueError("review.inbox.command must contain {script}")
        return [_shell(), "-lc", template.format(script=shlex.quote(str(script)))]
    raise ValueError(f"unknown review.inbox.terminal: {kind!r} (auto, terminal, iterm or command)")


def launch(target: Path, pr_ref: str, dry_run: bool = False) -> dict[str, Any]:
    """Start `/android-review N` in its own session in a new terminal tab, unless one already runs."""
    from android_workflow.review import parse_pr

    target = target.resolve()
    number = parse_pr(pr_ref)
    if session_alive(target, number):
        return {"status": "running", "pr": number, "next": "nothing: that session already reviews this PR"}
    config = inbox_config(target)
    script = write_script(target, number, config)
    command = open_command(script, config)
    if dry_run:
        return {"status": "dry_run", "pr": number, "script": str(script), "command": command}
    opened = subprocess.run(command, text=True, capture_output=True, check=False, timeout=60)
    if opened.returncode != 0:
        return {"status": "failed", "pr": number, "script": str(script),
                "error": (opened.stderr or opened.stdout).strip()[-400:],
                "next": f"tell the developer; they can run {_shell()} -l {script} in a terminal"}
    path = cache_dir(target) / STATE_FILE
    state = _read(path)
    entry = state.setdefault("prs", {}).setdefault(str(number), {"first_seen": time.time()})
    entry.update({"launched_at": time.time(), "launched_head": entry.get("head"),
                  "launches": int(entry.get("launches") or 0) + 1, "present": True})
    entry.pop("relaunch", None)
    _write(path, state)
    return {"status": "launched", "pr": number, "terminal": terminal_kind(config), "script": str(script),
            "tell": f"PR #{number}: review started in a new tab (session \"review PR-{number}\"); "
                    "it stops at the draft for your OK"}
