"""android-bug-triage: reproduce a reported bug on the device, find its cause, plan the fix, draft the ticket.

The triage is an ordinary android-workflow run of a `bug` ticket (`TARGET/.ai/workflow/<ID>/`), stopped
before any code is written. Its device step is the run's `before`: the Maestro flows that reproduce the
bug on the base APK are the flows `/android-workflow <ID>` replays `after` the fix, and its Planner pass
(mode `triage`) writes the plan that run continues from. Nothing is posted to a tracker without the
developer's OK on one exact draft id.

`triage next` answers the one thing to do now, like `next` and `review next`:

    start → fetch_ticket → prebuild → (device) repro → planner (cause + plan) → write_ticket → ask_user → done
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any

BUG_FILE = "bug-report.json"
STATE_FILE = "triage-state.json"
REPRO_FILE = "repro-report.md"
CAUSE_FILE = "cause.md"
REPORT_FILE = "triage-report.md"
TICKET_DRAFT = "ticket-draft.md"
TICKET_DIR = "ticket"
REPRO_BUDGET_SECONDS = 600
REPRO_STATUSES = ("REPRODUCED", "NOT_REPRODUCED", "NO_UI_PATH", "BLOCKED")

STACK_LINE = re.compile(r"^\s*at\s+[\w$.<>]+\(([\w$]+\.(?:kt|java)):(\d+)\)", re.M)
EXCEPTION_LINE = re.compile(r"^\s*(?:Caused by:\s*|FATAL EXCEPTION:.*\n\s*(?:Process:.*\n\s*)?)?"
                            r"((?:[a-z_][\w]*\.)+[A-Z][\w$]*(?:Exception|Error)\b[^\n]*)", re.M)
LINEAR_URL = re.compile(r"https?://linear\.app/[^\s/]+/issue/([A-Za-z][A-Za-z0-9]*-\d+)")
JIRA_URL = re.compile(r"https?://[^\s/]+\.atlassian\.net/browse/([A-Za-z][A-Za-z0-9]*-\d+)")

REDACTIONS = (
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "<email>"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"), "<jwt>"),
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{12,}"), r"\1 <token>"),
    (re.compile(r"(?i)\b((?:access_|refresh_|auth_|api_|session_)?(?:token|key|secret|password|passwd|cookie))"
                r"(\s*[=:]\s*)[\"']?[^\s\"'&,;]{6,}"), r"\1\2<redacted>"),
)


def redact(text: str) -> tuple[str, int]:
    """Emails, JWTs, bearer tokens and `token=`/`password:` values replaced; (text, how many)."""
    count = 0
    for pattern, replacement in REDACTIONS:
        text, hits = pattern.subn(replacement, text)
        count += hits
    return text, count


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _answer(next_step: str, why: str, **fields: Any) -> dict[str, Any]:
    return {"next": next_step, "why": why, **{key: value for key, value in fields.items() if value is not None}}


def stack_of(text: str) -> dict[str, Any] | None:
    """The exception and the `File.kt:N` frames of a pasted stack trace, or None when there is none."""
    frames = [f"{name}:{line}" for name, line in STACK_LINE.findall(text or "")]
    if not frames:
        return None
    exceptions = [match.strip() for match in EXCEPTION_LINE.findall(text)]
    return {"exception": exceptions[-1] if exceptions else None, "frames": list(dict.fromkeys(frames))[:15]}


def classify(text: str, config: dict[str, Any]) -> dict[str, Any]:
    """What the developer passed: a Linear or Jira ticket (key or link), a stack trace, or free text."""
    from android_workflow.review import jira_keys, ticket_source

    value = (text or "").strip()
    for pattern, tracker in ((LINEAR_URL, "linear"), (JIRA_URL, "jira")):
        match = pattern.search(value)
        if match:
            return {"kind": tracker, "key": match.group(1).upper(), "url": match.group(0)}
    stack = stack_of(value)
    first = value.split()[0] if value.split() else ""
    keys = jira_keys(first)
    if keys and not stack and len(value.split()) <= 12:
        # A key first, maybe with a few words after it: the ticket is the report.
        return {"kind": ticket_source(keys[0], config), "key": keys[0]}
    if stack:
        return {"kind": "stack", "key": keys[0] if keys else None, "stack": stack}
    return {"kind": "text", "key": keys[0] if keys else None}


def _slug(text: str) -> str:
    words = re.findall(r"[a-z0-9]+", text.lower())[:5]
    return "-".join(words) or "bug"


def triage_id(source: dict[str, Any], text: str) -> str:
    if source.get("key"):
        return str(source["key"])
    if source.get("stack", {}).get("exception"):
        name = str(source["stack"]["exception"]).split(":")[0].rsplit(".", 1)[-1]
        return f"BUG-{time.strftime('%Y%m%d')}-{_slug(name)}"
    return f"BUG-{time.strftime('%Y%m%d')}-{_slug(text)}"


def _title(source: dict[str, Any], text: str) -> str:
    if source.get("kind") in {"jira", "linear"}:
        return f"Bug triage {source['key']}"
    if source.get("stack", {}).get("exception"):
        return f"Crash: {source['stack']['exception']}"[:120]
    line = next((line.strip() for line in text.splitlines() if line.strip()), "Bug")
    return line[:120]


def _default_branch(target: Path) -> str:
    result = subprocess.run(["git", "-C", str(target), "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
                            capture_output=True, text=True, timeout=30, check=False)
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else "origin/main"


def fixed_hints(target: Path, key: str | None, frames: list[str]) -> list[dict[str, str]]:
    """Commits on the default branch that may already fix it: they name the key, or touched the top frame's
    file in the last 30 days. Hints for the agents, never a verdict."""
    branch = _default_branch(target)
    hints: list[dict[str, str]] = []
    queries: list[tuple[list[str], str]] = []
    if key:
        queries.append((["log", branch, "-i", f"--grep={key}", "--since=180 days", "--format=%h %s", "-n", "10"],
                        "names the key"))
    if frames:
        name = frames[0].split(":")[0]
        queries.append((["log", branch, "--since=30 days", "--format=%h %s", "-n", "10", "--", f"*{name}"],
                        f"touched {name} lately"))
    for args, why in queries:
        result = subprocess.run(["git", "-C", str(target), *args], capture_output=True, text=True, timeout=60, check=False)
        for line in result.stdout.splitlines():
            sha, _, subject = line.partition(" ")
            if sha and all(item["sha"] != sha for item in hints):
                hints.append({"sha": sha, "subject": subject[:160], "why": why})
    return hints[:10]


def _agent_dir(target: Path) -> Path:
    from android_workflow.paths import run_dir

    return run_dir(target)


def start(target: Path, text: str, no_device: bool = False) -> dict[str, Any]:
    """Create the bug run from whatever the developer passed and write `RUN/bug-report.json`."""
    from android_workflow import cli
    from android_workflow.review import TICKET_HOW, tickets_config

    if not (text or "").strip():
        raise ValueError("triage start needs --input: a ticket key or link, a stack trace or a description")
    source = classify(text, tickets_config(target))
    report_text, redacted = redact(text.strip())
    ticket_id = triage_id(source, report_text)
    ticket = {
        "id": ticket_id,
        "title": _title(source, report_text),
        "description": report_text[:4000],
        "type": "bug",
        # As reported until the device proves it: the triage Planner replaces it with the verified steps.
        "reproduction": "As reported (not verified yet): " + report_text[:1500],
        "acceptance_criteria": [],
        "surfaces": ["ui"],
        "complexity": "medium",
        "risk": "low",
        "explicit": ["type"],
        "business_questions": [],
    }
    state = cli.run(target, ticket, no_device=no_device)
    agent_dir = _agent_dir(target)
    stack = source.get("stack") or {}
    bug = {
        "schema_version": 1,
        "id": ticket_id,
        "source": source["kind"],
        "key": source.get("key"),
        "url": source.get("url"),
        "title": ticket["title"],
        "text": report_text,
        "redacted": redacted,
        "stack": stack or None,
        "fixed_hints": fixed_hints(target, source.get("key"), stack.get("frames") or []),
        "created_at": int(time.time()),
    }
    _write(agent_dir / BUG_FILE, bug)
    existing = _read(agent_dir / STATE_FILE)
    _write(agent_dir / STATE_FILE, {**existing, "started_at": existing.get("started_at") or int(time.time()),
                                    **({"no_device": True} if no_device else {})})
    with cli.run_lock(target):
        run_state = cli.load_state(target)
        run_state["mode"] = "triage"
        cli.save_state(target, run_state)
    cli.append_stage_log(agent_dir, "T1", "completed", "cli",
                         f"bug triage of {source['kind']} input" + (f" {source['key']}" if source.get("key") else ""))
    result = {"status": "ready", "id": ticket_id, "run": str(agent_dir), "source": source["kind"],
              "key": source.get("key"), "run_status": (state or {}).get("status")}
    if redacted:
        result["redacted"] = f"{redacted} email/token value(s) replaced in the report"
    if source["kind"] in {"jira", "linear"}:
        result["ticket_source"] = {"key": source["key"], "tracker": source["kind"], "how": TICKET_HOW}
    return result


def repro_status(agent_dir: Path) -> str | None:
    """`## Status: REPRODUCED` (or NOT_REPRODUCED, NO_UI_PATH, BLOCKED) from repro-report.md."""
    try:
        text = (agent_dir / REPRO_FILE).read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r"^##\s*Status:\s*([A-Z_]+)", text, re.M)
    return match.group(1) if match and match.group(1) in REPRO_STATUSES else None


def _planned(agent_dir: Path) -> bool:
    from android_workflow.cli import artifact_stub

    plan = agent_dir / "plan.md"
    if not (agent_dir / CAUSE_FILE).is_file() or not plan.is_file():
        return False
    return not artifact_stub("plan.md", plan.read_text(encoding="utf-8")) and \
        _read(agent_dir / "ticket-spec.json").get("planner_updated") is True


def _stage(target: Path, stage: str) -> str | None:
    from android_workflow.cli import load_state

    try:
        return ((load_state(target).get("stages") or {}).get(stage) or {}).get("status")
    except (OSError, ValueError, FileNotFoundError):
        return None


def _log_started(target: Path, stage: str, note: str, record: bool) -> None:
    if not record or _stage(target, {"Device": "T7", "Planner": "T3"}[stage]) in {"started", "running"}:
        return
    from android_workflow.cli import log_stage

    log_stage(target, stage, "started", note, "cli")


def draft_id(agent_dir: Path) -> str | None:
    try:
        text = (agent_dir / TICKET_DRAFT).read_text(encoding="utf-8")
    except OSError:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12] if text.strip() else None


def next_action(target: Path, record: bool = True, no_device: bool = False) -> dict[str, Any]:
    answer = _decide(target, record, no_device)
    if record:
        from android_workflow import decide

        try:
            agent_dir = _agent_dir(target)
            decide.settle_device(agent_dir, answer)
            with (agent_dir / "triage-decisions.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"at": round(time.time(), 3), "next": answer.get("next"),
                                         "why": answer.get("why")}, ensure_ascii=False) + "\n")
        except (OSError, FileNotFoundError):
            pass
    return answer


def _decide(target: Path, record: bool, no_device: bool) -> dict[str, Any]:
    from android_workflow.cli import load_state

    start_cmd = 'CLI triage start --target TARGET --input "<ticket key, link, stack trace or description>"'
    if not (target / ".ai" / "project-profile.md").is_file():
        return _answer("setup", "the project profile is missing: Setup runs once before the first triage",
                       command="CLI setup --target TARGET", then="CLI triage next --target TARGET")
    try:
        agent_dir = _agent_dir(target)
    except FileNotFoundError:
        return _answer("start", "no triage run here", command=start_cmd)
    bug = _read(agent_dir / BUG_FILE)
    if not bug:
        return _answer("start", "the current run is not a triage", command=start_cmd)
    state = _read(agent_dir / STATE_FILE)
    no_device = no_device or bool(state.get("no_device"))
    if state.get("posted") or state.get("post_skipped"):
        return _done(agent_dir, bug, state)
    run_state = load_state(target)
    if run_state.get("status") == "paused" and run_state.get("pending_question"):
        question = run_state["pending_question"]
        return _answer("ask_user", "the Planner needs an answer", question=question,
                       then=f'CLI resume --target TARGET --question-id {question.get("id")} --answer "…", '
                            "then resume aw-planner (mode triage) with the answer")

    # 1. The ticket text, when the input was a tracker key.
    if bug.get("source") in {"jira", "linear"} and not state.get("ticket_skipped") \
            and not (agent_dir / TICKET_DIR / f"{bug['key']}.md").is_file():
        from android_workflow.review import TICKET_HOW

        return _answer("fetch_ticket", "read the bug ticket before reproducing it", keys=[bug["key"]],
                       sources={bug["key"]: bug["source"]}, writes=[f"RUN/{TICKET_DIR}/{bug['key']}.md"],
                       how=TICKET_HOW.replace("CLI review ticket", "CLI triage ticket"),
                       then="CLI triage next --target TARGET")
    ticket_read = [f"RUN/{TICKET_DIR}/{path.name}" for path in sorted((agent_dir / TICKET_DIR).glob("*.md"))] \
        if (agent_dir / TICKET_DIR).is_dir() else []

    # 2. Reproduce on the device, on the base APK: the flows become the fix's `after` proof.
    status = repro_status(agent_dir)
    if status is None and not state.get("repro_skipped"):
        result = _repro_step(target, agent_dir, state, record, no_device, ticket_read)
        if result:
            return result
        state = _read(agent_dir / STATE_FILE)
    if status == "BLOCKED" and not state.get("blocked_accepted"):
        return _answer("ask_user", "the device could not run the reproduction (repro-report.md says why)",
                       question="The reproduction is BLOCKED (see repro-report.md). Fix the device and try again, "
                                "or go on to the cause from the code alone?",
                       options={"retry": "CLI triage repro --target TARGET --again, then CLI triage next --target TARGET",
                                "code only": "CLI triage repro --target TARGET --accept-blocked, then CLI triage next --target TARGET"})

    # 3. Cause and plan, unless the bug did not reproduce (then the report asks the reporter instead).
    if status != "NOT_REPRODUCED" and not _planned(agent_dir):
        planner = _stage(target, "T3")
        if planner in {"started", "running"}:
            return _answer("wait", "the Planner is finding the cause and writing the plan", agent="aw-planner",
                           if_returned='CLI log --target TARGET --stage Planner --status completed --note "cause"')
        resume = planner == "completed" and bool(state.get("planner_spawned"))
        _log_started(target, "Planner", "cause and plan for the bug", record)
        if record:
            state["planner_spawned"] = True
            _write(agent_dir / STATE_FILE, state)
        return _answer("spawn", "find the cause from the evidence and plan the fix"
                       + (": the Planner returned without cause.md, plan.md or update-spec" if resume else ""),
                       agent="aw-planner", resume=resume or None,
                       mode="triage", stage="Planner", repro=status or "skipped: " + str(state.get("repro_skipped")),
                       read=[f"RUN/{BUG_FILE}", *ticket_read]
                       + ([f"RUN/{REPRO_FILE}"] if status else []),
                       writes=[f"RUN/{CAUSE_FILE}", "RUN/plan.md", "CLI update-spec (AC, proofs, --reproduction)"])

    # 4. The report, then the ticket (or comment) text, shown before anything is posted.
    if not (agent_dir / REPORT_FILE).is_file() or state.get("report_stale"):
        return _answer("report", "every step is in: compose the triage report",
                       command="CLI triage report --target TARGET", then="CLI triage next --target TARGET")
    current = draft_id(agent_dir)
    if current is None:
        existing = bug.get("source") in {"jira", "linear"}
        return _answer("write_ticket", "turn the report into "
                       + (f"a comment on {bug['key']}" if existing else "a new ticket"),
                       skill="create-ticket" if not existing else None,
                       read=[f"RUN/{REPORT_FILE}"], writes=[f"RUN/{TICKET_DRAFT}"],
                       how=(f"a short comment for {bug['key']}: the result (reproduced or not, on which build and "
                            "device), the cause with file:line, the evidence paths and, when not reproduced, the "
                            "questions for the reporter" if existing else
                            "the create-ticket skill, with the report as its description; type bug, steps to "
                            "reproduce from the repro report") + ". English, no user data, no tool or AI names.",
                       then="CLI triage next --target TARGET")
    text = (agent_dir / TICKET_DRAFT).read_text(encoding="utf-8")
    clean, hits = redact(text)
    if hits:
        (agent_dir / TICKET_DRAFT).write_text(clean, encoding="utf-8")
        current = draft_id(agent_dir)
    if record and state.get("shown") != current:
        state["shown"] = current
        _write(agent_dir / STATE_FILE, state)
    where = (f"comment on {bug['key']} ({bug['source']})" if bug.get("source") in {"jira", "linear"}
             else "new ticket (the developer picks the tracker and team)")
    return _answer("ask_user", "show the draft; nothing is posted before the developer's OK",
                   question=f"Post this {where}?", show=f"RUN/{TICKET_DRAFT}", draft_id=current,
                   redacted=f"{hits} email/token value(s) replaced" if hits else None,
                   options={"post": "post it with the tracker's tool (Linear: save_comment / save_issue; Jira: "
                                    "addCommentToJiraIssue / createJiraIssue), then "
                                    f"CLI triage posted --target TARGET --draft-id {current} --url <link>",
                            "edit": f"edit RUN/{TICKET_DRAFT} as asked, then CLI triage next --target TARGET (new id)",
                            "stop": 'CLI triage posted --target TARGET --skip "<why>"'})


def _repro_step(target: Path, agent_dir: Path, state: dict[str, Any], record: bool, no_device: bool,
                ticket_read: list[str]) -> dict[str, Any] | None:
    from android_workflow import decide
    from android_workflow.prebuild import current_status

    def skip(reason: str) -> None:
        state["repro_skipped"] = reason
        if record:
            _write(agent_dir / STATE_FILE, state)

    if no_device:
        skip("the developer asked for --no-device")
        return None
    if _stage(target, "T7") in {"started", "running"} and not state.get("repro_again"):
        return _answer("wait", "the Device agent is reproducing the bug", agent="aw-device",
                       if_returned="CLI triage next --target TARGET (aw-device must write RUN/repro-report.md; "
                                   "resume it if it is missing)")
    build = current_status(target)
    if build.get("status") in {"not_started", None} or (build.get("status") == "failed" and not state.get("prebuild_retried")):
        if build.get("status") == "failed" and record:
            state["prebuild_retried"] = True
            _write(agent_dir / STATE_FILE, state)
        return _answer("prebuild", "the bug is reproduced on the base APK: build it (in its own checkout)",
                       command="CLI prebuild --target TARGET --wait --timeout 1800",
                       also=["CLI emulator --target TARGET"], then="CLI triage next --target TARGET")
    if build.get("status") == "running":
        return _answer("wait_prebuild", "the base build is still running", also=["CLI emulator --target TARGET"],
                       command="CLI prebuild --target TARGET --wait --timeout 1800", then="CLI triage next --target TARGET")
    if build.get("status") != "passed":
        skip(f"no base APK (prebuild {build.get('status')}: {build.get('reason') or 'see the prebuild log'})")
        return None
    serial, waiting, reason = decide._device_for(target, agent_dir, record)
    if waiting is not None:
        waiting["then"] = "CLI triage next --target TARGET"
        for option, command in list((waiting.get("options") or {}).items()):
            if "finish" in str(command):
                waiting["options"][option] = 'CLI triage repro --target TARGET --skip "<why>"'
        return waiting
    if not serial:
        skip(f"no device ({reason})")
        return None
    _log_started(target, "Device", "reproduce the bug on the base APK", record)
    if record:
        decide._mark_before(target, "spawned")
        state.pop("repro_again", None)
        _write(agent_dir / STATE_FILE, state)
    return _answer("spawn", "a base APK and a device are ready: reproduce the bug", agent="aw-device",
                   mode="repro", stage="Device", serial=serial, budget_seconds=REPRO_BUDGET_SECONDS,
                   scaffold=None if decide.flows_saved(agent_dir) else "CLI device scaffold --target TARGET",
                   read=[f"RUN/{BUG_FILE}", *ticket_read], writes=[f"RUN/{REPRO_FILE}", "RUN/media/flows/repro-*.yaml"],
                   done='CLI log --target TARGET --stage Device --status flows_ready --note "<status>"')


def _base_commit(target: Path) -> str | None:
    from android_workflow.paths import cache_dir

    return _read(cache_dir(target) / "repo-map.json").get("base_commit")


def report(target: Path) -> dict[str, Any]:
    """`RUN/triage-report.md`: what was reported, what the device showed, the cause and the plan, redacted."""
    agent_dir = _agent_dir(target)
    bug = _read(agent_dir / BUG_FILE)
    if not bug:
        raise ValueError("no triage run here: CLI triage start first")
    state = _read(agent_dir / STATE_FILE)
    spec = _read(agent_dir / "ticket-spec.json")
    status = repro_status(agent_dir)
    result = {"REPRODUCED": "reproduced on the device", "NOT_REPRODUCED": "not reproduced on the device",
              "NO_UI_PATH": "no UI path to reproduce it: cause from the code",
              "BLOCKED": "device blocked: cause from the code"}.get(status or "", "not run on a device: "
                                                                         + str(state.get("repro_skipped") or "unknown"))

    def section(name: str) -> str:
        try:
            return (agent_dir / name).read_text(encoding="utf-8").strip()
        except OSError:
            return "_(none)_"

    hints = bug.get("fixed_hints") or []
    lines = [
        f"# Bug triage: {bug.get('id')}",
        "",
        f"- Source: {bug.get('source')}" + (f" {bug.get('key')}" if bug.get("key") else ""),
        f"- Result: {result}",
        f"- Base build: {str(_base_commit(target) or 'the default branch')[:12]}",
        "",
        "## Reported",
        "",
        str(bug.get("text") or "").strip()[:3000],
        "",
    ]
    if bug.get("stack"):
        lines += ["## Stack", "", f"`{bug['stack'].get('exception')}`", "",
                  *[f"- {frame}" for frame in bug["stack"].get("frames") or []], ""]
    if hints:
        lines += ["## May already be fixed", "", *[f"- {item['sha']} {item['subject']} ({item['why']})" for item in hints], ""]
    lines += ["## Reproduction", "", section(REPRO_FILE), ""]
    if status != "NOT_REPRODUCED":
        acceptance = [f"- {item}" for item in spec.get("acceptance_criteria") or []] or ["_(no AC)_"]
        lines += ["## Cause", "", section(CAUSE_FILE), "", "## Plan", "", *acceptance, "", "Full plan: `plan.md`."]
    text, hits = redact("\n".join(lines) + "\n")
    (agent_dir / REPORT_FILE).write_text(text, encoding="utf-8")
    if state.get("report_stale"):
        state.pop("report_stale")
        _write(agent_dir / STATE_FILE, state)
    return {"status": "written", "path": str(agent_dir / REPORT_FILE), "result": status or "skipped",
            **({"redacted": hits} if hits else {})}


def posted(target: Path, draft: str | None, url: str | None, skip: str | None) -> dict[str, Any]:
    """Record the developer's decision: the draft they approved was posted (its id must match), or nothing."""
    from android_workflow.cli import append_stage_log

    agent_dir = _agent_dir(target)
    state = _read(agent_dir / STATE_FILE)
    if skip:
        state["post_skipped"] = skip
        _write(agent_dir / STATE_FILE, state)
        append_stage_log(agent_dir, "T8", "skipped", "host", f"triage ticket not posted: {skip}")
        return {"status": "skipped", "why": skip}
    current = draft_id(agent_dir)
    if not draft or draft != current:
        return {"status": "refused", "reason": "stale_draft",
                "detail": f"the draft is {current}, not {draft}: show it again (CLI triage next) and ask for the OK"}
    if state.get("shown") != current:
        return {"status": "refused", "reason": "not_shown", "detail": "this draft was never shown to the developer"}
    state["posted"] = {"draft_id": current, "url": url, "at": int(time.time())}
    _write(agent_dir / STATE_FILE, state)
    append_stage_log(agent_dir, "T8", "completed", "host", f"triage posted: {url or 'no link'}")
    return {"status": "posted", "url": url}


def repro(target: Path, again: bool = False, accept_blocked: bool = False, skip: str | None = None) -> dict[str, Any]:
    """The developer's call on the reproduction: run it again, go on from the code, or skip the device."""
    agent_dir = _agent_dir(target)
    state = _read(agent_dir / STATE_FILE)
    if again:
        (agent_dir / REPRO_FILE).unlink(missing_ok=True)
        state.update({"repro_again": True, "report_stale": True})
        state.pop("repro_skipped", None)
    if accept_blocked:
        state["blocked_accepted"] = True
    if skip:
        state["repro_skipped"] = skip
    _write(agent_dir / STATE_FILE, state)
    return {"status": "recorded", "state": {key: state[key] for key in ("repro_again", "blocked_accepted", "repro_skipped")
                                            if key in state}}


def ticket(target: Path, skip: str) -> dict[str, Any]:
    agent_dir = _agent_dir(target)
    state = _read(agent_dir / STATE_FILE)
    state["ticket_skipped"] = skip
    _write(agent_dir / STATE_FILE, state)
    return {"status": "skipped", "why": skip}


def _done(agent_dir: Path, bug: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    status = repro_status(agent_dir)
    fixable = status != "NOT_REPRODUCED" and _planned(agent_dir)
    return _answer("done", "the triage is over", report=f"RUN/{REPORT_FILE}",
                   posted=(state.get("posted") or {}).get("url"), result=status or "not run on a device",
                   offer=f"/android-workflow {bug.get('id')}" if fixable else None,
                   tell=(f"To fix it: /android-workflow {bug.get('id')} continues this run (plan ready; the repro flows "
                         "become the after proof)." if fixable else
                         "Not reproduced: the report lists what was tried and the questions for the reporter."),
                   also=["CLI emulator --target TARGET --stop"])


def dispatch(target: Path, args: Any) -> tuple[dict[str, Any], int]:
    action = args.triage_action
    if action == "start":
        result = start(target, args.input or "", no_device=args.no_device)
        return result, 0
    if action == "next":
        return next_action(target, record=not args.dry_run, no_device=args.no_device), 0
    if action == "report":
        return report(target), 0
    if action == "posted":
        result = posted(target, args.draft_id, args.url, args.skip)
        return result, 0 if result["status"] in {"posted", "skipped"} else 1
    if action == "repro":
        return repro(target, again=args.again, accept_blocked=args.accept_blocked, skip=args.skip), 0
    if action == "ticket":
        if not args.skip:
            raise ValueError("triage ticket needs --skip")
        return ticket(target, args.skip), 0
    raise ValueError(f"unknown triage action: {action}")
