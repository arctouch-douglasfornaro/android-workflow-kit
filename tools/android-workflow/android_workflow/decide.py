"""Decisions the run's files already answer, made by code instead of the orchestrator's model.

`choose_level` picks express, standard or full from the ticket spec. `next_action` reads the run
(state, verdicts, fingerprints, round counts) and returns the one thing to do now. Both are
deterministic: the same files always give the same answer, and every answer carries a `why`.
When the files cannot settle a question, the answer is the safe one (run the Planner, a single
Implementer, a draft PR), never a guess that skips a check.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from android_workflow.paths import run_dir

LEVELS = ("express", "standard", "full")
FULL_SURFACES = frozenset({"lifecycle", "room_migration", "workmanager"})
# Ticket words that make a change high risk, English and Portuguese.
FULL_WORDS = frozenset({
    "payment", "payments", "billing", "purchase", "purchases", "subscription", "subscriptions",
    "auth", "authentication", "login", "logout", "signin", "oauth", "password",
    "pagamento", "pagamentos", "assinatura", "assinaturas", "compra", "compras", "autenticação",
    "autenticacao", "senha",
})
EXPRESS_TYPES = frozenset({"chore", "feature"})
TEAM_MIN_PARTS = 2
TEAM_MIN_FILES = 4
GATE_MAX_FIXES = 2      # the third red gate opens a draft PR
REVIEW_MAX_FIXES = 1    # still blocking after one fix round: draft
DEVICE_MAX_FAILS = 2    # the second device FAIL: draft
BEFORE_BUDGET_SECONDS = 600  # the Device `before` agent stops here: the Implementer is working meanwhile
AFTER_BUDGET_SECONDS = 300   # the Device `after` agent: replay, judge, a few hand-driven checks
DECISIONS_FILE = "decisions.jsonl"
BOOTING = "booting"  # how a `wait_device` for a device starting for this run begins its `why`


# --- level -------------------------------------------------------------------------------------

def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-zà-ÿ]+", text.lower()))


def choose_level(spec: dict[str, Any], change_set: dict[str, Any] | None = None) -> dict[str, Any]:
    """`full` or `standard` from the rules, and whether `express` is allowed.

    The rules are code; only "is this a ≤3-file change on a named screen" needs reading the ticket,
    so the CLI never picks express by itself: it says when express is allowed, and refuses it
    otherwise (a bug, an open question, risk or complexity above low, a risky surface or word).
    """
    ticket = spec.get("ticket") or {}
    text = f"{ticket.get('title') or ''} {ticket.get('description') or ''}"
    surfaces = set(spec.get("surfaces") or [])
    risk, complexity = str(spec.get("risk") or "low"), str(spec.get("complexity") or "low")
    full = []
    if risk == "high":
        full.append("risk high")
    if complexity == "high":
        full.append("complexity high")
    if surfaces & FULL_SURFACES:
        full.append("surface " + ",".join(sorted(surfaces & FULL_SURFACES)))
    words = _words(text) & FULL_WORDS
    if words:
        full.append("ticket mentions " + ",".join(sorted(words)))
    if full:
        return {"level": "full", "reasons": full, "express_allowed": False}
    blockers = []
    if spec.get("type") == "bug":
        blockers.append("a bug: the Planner checks the reproduction")
    elif spec.get("type") not in EXPRESS_TYPES:
        blockers.append(f"type {spec.get('type')}")
    if spec.get("business_questions"):
        blockers.append("an open business question")
    if risk != "low" or complexity != "low":
        blockers.append(f"risk {risk}, complexity {complexity}")
    if blockers:
        return {"level": "standard", "reasons": blockers, "express_allowed": False}
    return {"level": "standard", "reasons": [f"{spec.get('type')}, low risk"], "express_allowed": True}


RANK = {level: index for index, level in enumerate(LEVELS)}


def apply_level(spec: dict[str, Any], change_set: dict[str, Any] | None = None, explicit: str | None = None,
                planner: bool = False) -> dict[str, Any]:
    """Write the level into the spec. The developer's `--level` wins. Otherwise the rules only ever
    raise it: to `full` when a risky surface or word appears, out of express when express is no
    longer allowed (a bug, a question); a Planner may not set a level below what the rules give."""
    chosen = choose_level(spec, change_set)
    spec["express_allowed"] = chosen["express_allowed"]
    if explicit:
        if planner and RANK[explicit] < RANK[chosen["level"]] and not (explicit == "express" and chosen["express_allowed"]):
            raise ValueError(f"level {explicit} is below what the rules give ({chosen['level']}: "
                             + "; ".join(chosen["reasons"]) + ")")
        if planner and explicit == "express" and not chosen["express_allowed"]:
            raise ValueError("express is not allowed for this ticket: " + "; ".join(chosen["reasons"]))
        spec.update({"level": explicit, "level_reasons": ["set by the Planner" if planner else "--level"],
                     "level_source": "planner" if planner else "flag"})
        return spec
    current = spec.get("level")
    if spec.get("level_source") == "flag" and current in RANK:
        return spec
    raise_to = None
    if chosen["level"] == "full" and current != "full":
        raise_to = "full"
    elif current == "express" and not chosen["express_allowed"]:
        raise_to = "standard"
    elif current not in RANK:
        raise_to = chosen["level"]
    if raise_to:
        spec.update({"level": raise_to, "level_reasons": chosen["reasons"], "level_source": "cli"})
    return spec


# --- run state helpers -----------------------------------------------------------------------

def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def verdict_history(stage_state: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in stage_state.get("history") or [] if isinstance(item, dict)]


def failed_rounds(stage_state: dict[str, Any], bad: set[str], current: str | None, fingerprint: str | None) -> int:
    """Distinct sources with a bad verdict since the last good one (a green verdict resets the count)."""
    seen: set[Any] = set()
    for item in verdict_history(stage_state):
        if item.get("verdict") in bad:
            seen.add(item.get("fingerprint"))
        else:
            seen.clear()
    if current in bad and fingerprint:
        seen.add(fingerprint)
    return len(seen) or (1 if current in bad else 0)


def gate_reds(gate: dict[str, Any]) -> int:
    """Red gates in a row (the gate records its streak; older reports fall back to their failed runs)."""
    if isinstance(gate.get("red_streak"), int):
        return int(gate["red_streak"])
    prior = [item for item in gate.get("prior_runs") or [] if item.get("status") == "failed"]
    return len(prior) + (1 if gate.get("status") == "failed" else 0)


def gate_failures(gate: dict[str, Any]) -> list[str]:
    from android_workflow.cli import step_passes

    failed = [step for step in gate.get("steps") or [] if not step_passes(step)]
    ids = [f"G{index}" for index, _ in enumerate(failed, 1)]
    if (gate.get("secret_scan") or {}).get("status") == "failed":
        ids.append("G-secrets")
    return ids or (["G1"] if gate.get("status") == "failed" else [])


def review_blocking(review: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in review.get("blocking") or [] if isinstance(item, dict)]


def parallel_plan(spec: dict[str, Any], plan_text: str) -> tuple[int, int]:
    """(parts, files) of the Planner's Parallel work. Recorded by `update-spec`, else read from plan.md."""
    if isinstance(spec.get("parallel_parts"), int):
        return int(spec["parallel_parts"]), int(spec.get("parallel_files") or 0)
    line = next((row for row in plan_text.splitlines() if "parallel work" in row.lower()), "")
    if not line or "single" in line.lower():
        return 1, 0
    return 1, 0  # free text the code cannot count: one Implementer (the safe choice)


def visual(spec: dict[str, Any]) -> bool:
    if isinstance(spec.get("visual"), bool):
        return bool(spec["visual"])
    return "ui" in (spec.get("surfaces") or [])


def owner_of(path: str, owners: dict[str, str]) -> str | None:
    clean = path.split(":", 1)[0].strip().lstrip("./")
    for file, slice_id in owners.items():
        if clean == file or clean.endswith("/" + file) or file.endswith("/" + clean):
            return slice_id
    return None


# --- the next action -------------------------------------------------------------------------

def _answer(next_step: str, why: str, **fields: Any) -> dict[str, Any]:
    return {"next": next_step, "why": why, **{key: value for key, value in fields.items() if value not in (None, [], {})}}


def _spawn(agent: str, why: str, **fields: Any) -> dict[str, Any]:
    return _answer("spawn", why, agent=agent, **fields)


def _draft(why: str, state: dict[str, Any] | None = None) -> dict[str, Any]:
    if state and state.get("finished_fingerprint"):
        # A PR feedback round: the PR already exists. Push nothing; the developer decides.
        return _answer("ask_user", why + " (PR round)", question="push nothing: tell the developer what is still open",
                       then="CLI pr wait --target TARGET")
    return _answer("finish", why, command=f'CLI finish --target TARGET --draft "{why}"', draft=why)


def next_action(target: Path, no_device: bool = False, record: bool = True, no_watch: bool = False,
                level: str | None = None) -> dict[str, Any]:
    """The one thing the orchestrator does now. With `record`, it is appended to `decisions.jsonl`
    and the stage it hands to an agent is logged as started (once). `level` records the
    orchestrator's express/standard choice when the rules left it open."""
    agent_dir = run_dir(target)
    if not (agent_dir / "run-state.json").is_file():
        return _answer("start", "no run for this ticket yet", command="CLI start --target TARGET --id ID --title \"…\"")
    if level and record:
        choose(target, agent_dir, level)
    answer = _decide(target, agent_dir, no_device, no_watch, record)
    if record:
        _record(target, agent_dir, answer)
    return answer


def choose(target: Path, agent_dir: Path, level: str) -> None:
    from android_workflow.cli import run_lock

    with run_lock(target):
        _choose(agent_dir, level)


def _choose(agent_dir: Path, level: str) -> None:
    from android_workflow.cli import append_stage_log, write_json

    path = agent_dir / "ticket-spec.json"
    spec = _read(path)
    if level not in LEVELS:
        raise ValueError(f"level must be one of {', '.join(LEVELS)}")
    if level == "express" and not spec.get("express_allowed"):
        raise ValueError("express is not allowed for this ticket: " + "; ".join(spec.get("level_reasons") or []))
    if RANK.get(level, 0) < RANK.get(str(spec.get("level")), 0) and level != "express":
        raise ValueError(f"the rules already set level {spec.get('level')}; it is never lowered")
    spec.update({"level": level, "level_reasons": ["chosen by the orchestrator"], "level_source": "orchestrator"})
    write_json(path, spec)
    append_stage_log(agent_dir, "T1", "completed", "host", f"level {level}")


def _decide(target: Path, agent_dir: Path, no_device: bool, no_watch: bool = False, record: bool = True) -> dict[str, Any]:
    from android_workflow.cli import (
        implementation_errors,
        load_state,
        recorded_verdict,
        source_fingerprint,
    )

    state = load_state(target)
    spec = _read(agent_dir / "ticket-spec.json")
    stages = state.get("stages") or {}
    if state.get("status") == "paused" and state.get("pending_question"):
        question = state["pending_question"]
        return _answer("ask_user", "a business question is open", question=question,
                       then=f'CLI resume --target TARGET --question-id {question.get("id")} --answer "…"')
    if state.get("status") == "escalated" and state.get("current_stage") == "T3":
        return _answer("ask_user", "a bug needs a reproduction before planning",
                       then='CLI update-spec --target TARGET --reproduction "…", then next')
    if state.get("status") == "completed":
        delivery = _read(agent_dir / "delivery.json")
        if not delivery and (stages.get("T8") or {}).get("status") == "started":
            return _answer("wait", "Delivery is writing the PR description and opening the PR", agent="aw-delivery")
        if not delivery:
            return _spawn("aw-delivery", "finish passed; the PR body and the PR are next", stage="Delivery",
                          note="writing the PR description")
        stop = ["CLI emulator --target TARGET --stop"] if "T7" in (spec.get("route") or []) else []
        if delivery.get("pr") and not no_watch and not _read(agent_dir / "pr-watch.json").get("ended"):
            return _answer("watch", "the PR is open", command="CLI pr wait --target TARGET", also=stop)
        return _answer("done", "the run is delivered", command="CLI status --target TARGET", also=stop)

    if record and not no_device and "T7" in (spec.get("route") or []):
        _want_device(agent_dir)
    level = str(spec.get("level") or "standard")
    planner = stages.get("T3") or {}
    if (level == "standard" and spec.get("express_allowed") and spec.get("level_source") == "cli"
            and not spec.get("planner_updated") and planner.get("status") != "started"):
        return _answer(
            "choose_level", "the rules allow express; only the ticket text says whether it fits",
            rule="express when the ticket names the screen or file and the change touches 3 files or fewer; "
                 "otherwise standard",
            express="CLI next --target TARGET --level express", standard="CLI next --target TARGET --level standard",
        )
    if level == "express" and not spec.get("planner_updated") and not spec.get("acceptance_criteria"):
        return _answer("update_spec", "express skips the Planner: record the AC and surfaces from the ticket text",
                       command='CLI update-spec --target TARGET --acceptance "a|b" --surfaces ui', then="CLI next --target TARGET")
    planner_done = (bool(spec.get("planner_updated")) or planner.get("note") is not None and planner.get("status") == "completed") \
        and planner.get("status") != "started" and not spec.get("replan")
    if level != "express" and not planner_done:
        if planner.get("status") == "started":
            return _answer("wait", "the Planner is working", agent="aw-planner")
        if spec.get("replan"):
            answered = [q for q in spec.get("business_questions") or [] if q.get("answer") is not None]
            return _spawn("aw-planner", "the developer answered the Planner's questions: finish the plan with them",
                          stage="Planner", level=level, answers=answered, resume=True,
                          note="finish the plan with the developer's answers")
        return _spawn("aw-planner", f"level {level}: plan before code", stage="Planner", level=level,
                      note="turn the ticket into verifiable AC and confirm the change set",
                      also=_background(spec, no_device))

    implementer = stages.get("T4") or {}
    lead = stages.get("T4L") or {}
    team = _read(agent_dir / "team-plan.json")
    if implementer.get("status") == "integrating" or (implementer.get("slices") and lead.get("status") != "completed"
                                                       and all((item or {}).get("status") == "completed"
                                                               for item in implementer["slices"].values())
                                                       and not implementer.get("integrated")):
        if lead.get("status") == "started":
            return _answer("wait", "the Tech Lead is integrating", agent="aw-tech-lead")
        return _spawn("aw-tech-lead", "every slice is done: integrate them before the gate", stage="Tech Lead",
                      mode="integrate", note="integrate the slices")
    working = [sid for sid, item in (implementer.get("slices") or {}).items() if (item or {}).get("status") == "started"]
    if implementer.get("status") == "started" or working:
        device = _device_before(target, agent_dir, spec, state, no_device, record)
        if device:
            return device
        done = not working and not implementation_errors(target)
        return _answer("wait", "the Implementer is working" + (f" ({', '.join(working)})" if working else "")
                       + ("; its notes look complete" if done else ""), agent="aw-implementer",
                       # A returned agent whose `completed` was never logged leaves `next` waiting forever.
                       if_returned='CLI log --target TARGET --stage Implementer --status completed --note "<summary>", '
                                   "then CLI next --target TARGET" if done else None)
    if lead.get("status") == "started":
        return _answer("wait", "the Tech Lead is working", agent="aw-tech-lead")

    errors = implementation_errors(target)
    if errors and implementer.get("status") != "completed":
        parts, files = parallel_plan(spec, (agent_dir / "plan.md").read_text(encoding="utf-8") if (agent_dir / "plan.md").is_file() else "")
        if level != "express" and parts >= TEAM_MIN_PARTS and files >= TEAM_MIN_FILES and not team:
            return _spawn("aw-tech-lead", f"{parts} independent parts, {files} files: split for parallel Implementers",
                          stage="Tech Lead", mode="split", note="split the plan into slices", then="CLI team --target TARGET")
        if team and len(team.get("slices") or []) >= 2 and not implementer.get("slices"):
            ids = [item.get("id") for item in team["slices"] if isinstance(item, dict)]
            return _spawn("aw-implementer", "the team plan is ready: one Implementer per slice, in the same turn",
                          stage="Implementer", slices=ids, level=level, note="build its slice")
        return _spawn("aw-implementer", "write the change and the tests that prove it", stage="Implementer", level=level,
                      note="implement the change and its unit tests", also=_background(spec, no_device, implementer=True))
    if errors:
        return _spawn("aw-implementer", "the Implementer reported done but " + "; ".join(errors), stage="Implementer",
                      level=level, note="finish the change")

    fingerprint = source_fingerprint(target)
    gate = _read(agent_dir / "gate-report.json")
    review = _read(agent_dir / "review.json")
    review_state = stages.get("T6") or {}
    review_verdict = recorded_verdict(agent_dir, "T6")
    gate_now = gate.get("status") in {"passed", "failed", "blocked"} and gate.get("fingerprint") == fingerprint
    review_now = review_verdict in {"approved", "changes_requested"} and review_state.get("fingerprint") == fingerprint
    if not review_now and review_verdict == "approved" and review_state.get("fingerprint"):
        kept = _keep_review_if_formatting_only(target, agent_dir, state, record)
        if kept:
            review_now = True
    gate_running = (stages.get("T5") or {}).get("status") == "running"
    reviewer_running = review_state.get("status") == "started"
    if gate_now and gate.get("status") == "blocked":
        return _draft("the quality gate is blocked: " + str(gate.get("reason") or "see gate-report.json"), state)
    if not gate_now or not review_now:
        todo = []
        if not gate_now and not gate_running:
            todo.append("gate")
        if not review_now and not reviewer_running:
            todo.append("reviewer")
        if not todo:
            return _answer("wait", "the gate and the Reviewer are running")
        delta = bool(review_state.get("reviewed_tree")) and review_verdict in {"approved", "changes_requested"}
        return _answer(
            "checks", "the change has no gate and review for its current source yet", run=todo,
            command="CLI gate --target TARGET" if "gate" in todo else None,
            agent="aw-reviewer" if "reviewer" in todo else None,
            mode=("delta" if delta else "full") if "reviewer" in todo else None,
            tier="standard" if delta and "reviewer" in todo else None,
            fixed=[item.get("id") for item in review_blocking(review)] if delta and "reviewer" in todo else None,
            stage="Reviewer" if "reviewer" in todo else None,
            note=("delta review of the fix" if delta else "independent review of the diff") if "reviewer" in todo else None,
            also=["CLI device build --target TARGET (background: the APK the Device installs, built before it "
                  "takes a device)"] if "gate" in todo and "T7" in (spec.get("route") or []) and not no_device else None,
        )

    findings: list[str] = []
    gate_failed = gate.get("status") == "failed"
    review_failed = review_verdict == "changes_requested"
    if gate_failed and gate_reds(gate) > GATE_MAX_FIXES:
        return _draft(f"the quality gate is still red after {GATE_MAX_FIXES} fix rounds", state)
    review_rounds = failed_rounds(review_state, {"changes_requested"}, review_verdict, review_state.get("fingerprint"))
    if review_failed and review_rounds > REVIEW_MAX_FIXES and not (
            review_rounds <= REVIEW_MAX_FIXES + 1
            and _only_new_findings(agent_dir, review, float(state.get("created_at") or 0))):
        return _draft(f"the review still blocks after {REVIEW_MAX_FIXES} fix round", state)
    if gate_failed:
        findings.extend(gate_failures(gate))
    if review_failed:
        findings.extend(str(item.get("id")) for item in review_blocking(review) if item.get("id"))
    if gate_failed or review_failed:
        if _fixed_before(agent_dir, fingerprint, float(state.get("created_at") or 0)):
            return _draft("a fix round ended without changing the code", state)
        return _fix_round(agent_dir, team, findings, review, "the gate or the review found problems", fingerprint,
                          sources=[name for name, bad in (("gate-report.json", gate_failed), ("review.json", review_failed)) if bad])

    route = spec.get("route") or []
    if "T7" in route:
        device_state = stages.get("T7") or {}
        device_verdict = recorded_verdict(agent_dir, "T7")
        device_now = device_state.get("fingerprint") == fingerprint and device_verdict in {"pass", "fail", "blocked", "partial"}
        if device_now and device_verdict in {"pass", "partial"}:
            late = _late_before(target, agent_dir, spec, state, record)
            if late:
                return late
        if device_now and device_verdict == "pass":
            return _finish(spec)
        if device_now and device_verdict == "partial":
            if state.get("finished_fingerprint"):  # a PR round: the developer decides, nothing is pushed yet
                return _answer("ask_user", "the device check is partial (PR round)", question=PARTIAL_QUESTION,
                               options=_partial_options(), then="CLI next --target TARGET")
            return _answer("ask_user", "the device check is partial: some AC were not verified on the device",
                           question=PARTIAL_QUESTION, options=_partial_options())
        if device_now and device_verdict == "blocked":
            return _draft("the device check is blocked: see device-report.md", state)
        fails = failed_rounds(device_state, {"fail"}, device_verdict if device_now else None, fingerprint)
        if device_now and device_verdict == "fail":
            if fails >= DEVICE_MAX_FAILS:
                return _draft(f"the device check failed {fails} times", state)
            if _fixed_before(agent_dir, fingerprint, float(state.get("created_at") or 0)):
                return _draft("a fix round ended without changing the code", state)
            return _fix_round(agent_dir, team, ["D1"], review, "the device check failed", fingerprint,
                              sources=["device-report.md"])
        if device_state.get("status") == "started":
            return _answer("wait", "the Device agent is working", agent="aw-device")
        if no_device:
            return _finish(spec, skip="the developer asked for --no-device", no_device=True)
        serial, waiting, reason = _device_for(target, agent_dir, record)
        if waiting is not None:
            return waiting
        if serial:
            return _spawn("aw-device", "the change passed the gate and the review: verify it on the device",
                          stage="Device", mode="after", serial=serial,
                          replay=_replay_hint(agent_dir),
                          scaffold=None if flows_saved(agent_dir) else "CLI device scaffold --target TARGET",
                          budget_seconds=AFTER_BUDGET_SECONDS,
                          note="after: verify the AC on the device")
        return _finish(spec, skip=f"no device: {reason}")
    return _finish(spec)


def _want_device(agent_dir: Path) -> None:
    """Tell the machine this run has a Device stage ahead, so a free emulator stays open for it."""
    from android_workflow.locks import want

    try:
        want(agent_dir)
    except OSError:
        pass


def _finish(spec: dict[str, Any], skip: str | None = None, no_device: bool = False) -> dict[str, Any]:
    if no_device:
        return _answer("finish", "every check passed", command="CLI finish --target TARGET --no-device")
    if skip:
        return _answer("finish", "every check passed; " + skip, command=f'CLI finish --target TARGET --skip-device "{skip}"')
    return _answer("finish", "every check passed", command="CLI finish --target TARGET")


def _fixed_before(agent_dir: Path, fingerprint: str, since: float = 0) -> bool:
    """A fix round of this run was already handed out for exactly this source: it came back without
    a change. Decisions from before this run's `start` do not count."""
    try:
        lines = (agent_dir / DECISIONS_FILE).read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    for line in lines:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if item.get("fix_of") == fingerprint and float(item.get("at") or 0) >= since:
            return True
    return False


def _only_new_findings(agent_dir: Path, review: dict[str, Any], since: float = 0) -> bool:
    """Every blocking finding is one no fix round of this run was handed (a delta review keeps the IDs
    of what is still open): the fixes worked and the delta found something else, so one more round."""
    current = {str(item.get("id")) for item in review_blocking(review) if item.get("id")}
    if not current:
        return False
    handed: set[str] = set()
    try:
        lines = (agent_dir / DECISIONS_FILE).read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    for line in lines:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if item.get("fix_of") and float(item.get("at") or 0) >= since:
            handed.update(str(value) for value in item.get("ids") or [])
    return bool(handed) and not current & handed


def _fix_round(agent_dir: Path, team: dict[str, Any], findings: list[str], review: dict[str, Any], why: str,
               fingerprint: str, sources: list[str]) -> dict[str, Any]:
    from android_workflow.team import owners as plan_owners

    if team and len(team.get("slices") or []) >= 2:
        owners = plan_owners(team)
        routed: dict[str, list[str]] = {}
        for item in review_blocking(review):
            if str(item.get("id")) in findings:
                where = str(item.get("file") or item.get("location") or "")
                routed.setdefault(owner_of(where, owners) or "Tech Lead", []).append(str(item["id"]))
        rest = [item for item in findings if not any(item in ids for ids in routed.values())]
        if rest:
            routed.setdefault("Tech Lead", []).extend(rest)
        return _answer("fix", why, agent="aw-implementer", routes=routed, read=sources, stage="Implementer",
                       note="fix round: " + ", ".join(findings))
    return _spawn("aw-implementer", why, mode="fix", ids=findings, read=sources, stage="Implementer",
                  note="fix round: " + ", ".join(findings), resume=True, fix_of=fingerprint)


def _background(spec: dict[str, Any], no_device: bool, implementer: bool = False) -> list[str]:
    if "T7" not in (spec.get("route") or []) or no_device:
        return []
    # A change with nothing to see starts no base build: it writes its flows in `after` (or in `before`
    # when another run's base build is already cached) and leaves the build slot to the gates.
    steps = ["CLI emulator --target TARGET", *(["CLI prebuild --target TARGET"] if visual(spec) else [])]
    if implementer:
        steps.append("CLI next --target TARGET (in the same turn: it says whether to capture the device `before`)")
    return steps


def _device_before(target: Path, agent_dir: Path, spec: dict[str, Any], state: dict[str, Any], no_device: bool,
                   record: bool = True) -> dict[str, Any] | None:
    """While the Implementer works: the Device `before` run, once, when a real base build exists.

    It captures the current screen (a visual change) and, whatever the change, writes the Maestro flows the
    `after` run replays by code. The Implementer edits meanwhile, and the gate and the review need no device,
    so this time comes out of the wait before `after`, not out of the run."""
    if no_device or "T7" not in (spec.get("route") or []) or state.get("device_before"):
        return None
    if any((agent_dir / "media" / "before").glob("*")) if (agent_dir / "media" / "before").is_dir() else False:
        return None
    if flows_saved(agent_dir):
        return None
    from android_workflow.prebuild import current_status

    build = current_status(target)
    if build.get("status") == "running":
        # Bounded: the Implementer may return first, and then the run moves on (the late before
        # replays the flows on the base APK once it exists).
        return _answer("wait_prebuild", "the base build is still running; the before capture needs it",
                       command="CLI prebuild --target TARGET --wait --timeout 600", then="CLI next --target TARGET")
    if build.get("status") != "passed":
        return _skip_before(target, f"no base APK (prebuild {build.get('status')})", record)
    serial, waiting, reason = _device_for(target, agent_dir, record)
    if waiting is not None:
        # Booting for this run: wait for it. Every device busy: the late `before` (after the `after`) covers it.
        return waiting if waiting["why"].startswith(BOOTING) else None
    if not serial:
        return _skip_before(target, f"no device ({reason})", record)
    if record:
        _mark_before(target, "spawned")
    capture = visual(spec)
    return _spawn("aw-device", "a base APK and a device are ready: "
                  + ("capture the before state and write the flows the after run replays" if capture
                     else "write the flows the after run replays"),
                  stage="Device", mode="before", serial=serial, capture=capture,
                  budget_seconds=BEFORE_BUDGET_SECONDS, scaffold="CLI device scaffold --target TARGET",
                  done='CLI log --target TARGET --stage Device --status flows_ready --note "<n> flow(s) saved"',
                  note="before: capture the current screen and write the flows" if capture else "before: write the flows")


PARTIAL_QUESTION = ("The device check is partial: device-report.md lists AC it could not verify (Not verified) or "
                    "something it could not explain (Anomalies). Open the PR ready for review with that disclosed, "
                    "open it as a draft, or run the Device again on what is open?")


def _partial_options() -> dict[str, str]:
    return {
        "ready": 'CLI finish --target TARGET --accept-partial "<what stays unverified, in one line>"',
        "draft": 'CLI finish --target TARGET --draft "device check partial: see device-report.md"',
        "again": "spawn aw-device mode after with the report's Not verified and Anomalies items, then CLI next",
    }


def _device_for(target: Path, agent_dir: Path, record: bool) -> tuple[str | None, dict[str, Any] | None, str]:
    """(serial, wait answer, reason): the device leased to this run for the rest of it, or the
    `wait_device` answer (`BOOTING` in its `why` when a device is starting for this run, not busy), or
    neither and the reason there is no device. A device nobody uses is reused; with every device taken, one more emulator
    starts for this run (`device.max_emulators`). Two runs never install their builds on one device."""
    from android_workflow.emulator import claim

    try:
        current = claim(target, record=record)
    except Exception as error:  # noqa: BLE001 - no SDK: no device
        current = {"status": "unavailable", "reason": f"adb is not available ({error})"}
    state = current.get("status")
    if state in {"ready", "connected"}:
        return str(current["serial"]), None, ""
    if state in {"booting", "would_start"}:
        what = (f"the emulator {current.get('serial')} ({current.get('avd')}) is booting for this run"
                if current.get("started_by_workflow") else "a connected device is still starting") \
            if state == "booting" else "no device is free: one more emulator starts for this run"
        return None, _answer("wait_device", BOOTING + ": " + what, command="CLI emulator --target TARGET --wait",
                             then="CLI next --target TARGET"), ""
    if state == "busy":
        holders = current.get("holders") or {}
        return None, _answer("wait_device", "every device is in use: " + (", ".join(
            f"{serial} ({label})" for serial, label in holders.items()) or str(current.get("reason"))),
            reason=current.get("reason"), command="CLI emulator --target TARGET --wait-free",
            then="CLI next --target TARGET"), ""
    if state == "unauthorized":
        return None, _answer("ask_user", "a phone waits for its USB-debugging prompt",
                             question="accept USB debugging on the phone", then="CLI next --target TARGET"), ""
    return None, None, str(current.get("reason") or state or "no device")


def _late_before(target: Path, agent_dir: Path, spec: dict[str, Any], state: dict[str, Any],
                 record: bool) -> dict[str, Any] | None:
    """The `before` the run could not take while the Implementer worked (no base APK yet, or the
    device was busy): the `after` round saved its flows, so the base APK replays them by code, with
    no agent. Visual changes only, once per run."""
    if not visual(spec) or not flows_saved(agent_dir) or (agent_dir / "replay-before.json").is_file():
        return None
    before = agent_dir / "media" / "before"
    if before.is_dir() and any(before.iterdir()):
        return None
    if str(state.get("device_before") or "").startswith(("late: replaying", "late: skipped")):
        return None  # tried once already
    from android_workflow.prebuild import current_status

    build = current_status(target)
    if build.get("status") == "running":
        return _answer("wait_prebuild", "the base build is still running; the late before capture needs it",
                       command="CLI prebuild --target TARGET --wait --timeout 900", then="CLI next --target TARGET")
    if build.get("status") != "passed":
        if build.get("status") == "failed" or state.get("device_before") == "late: building":
            if record:
                _mark_before(target, f"late: skipped, no base APK (prebuild {build.get('status')})")
            return None
        if record:
            _mark_before(target, "late: building")
        return _answer("wait_prebuild", "no base APK yet: build the base for the late before capture",
                       command="CLI prebuild --target TARGET --force --wait", then="CLI next --target TARGET")
    serial, waiting, reason = _device_for(target, agent_dir, record)
    if waiting is not None and waiting["why"].startswith(BOOTING):
        return waiting
    if not serial:
        if record:
            _mark_before(target, f"late: skipped, no device ({reason or 'every device is in use'})")
        return None
    if record:
        _mark_before(target, "late: replaying")
    return _answer("replay_before", "the after flows exist: replay them on the base APK for the before captures",
                   command=f"CLI device replay --target TARGET --serial {serial} --phase before --install-base",
                   then="CLI next --target TARGET")


def _skip_before(target: Path, reason: str, record: bool = True) -> None:
    if record:
        _mark_before(target, "skipped: " + reason)
    return None


def _mark_before(target: Path, value: str) -> None:
    from android_workflow.cli import load_state, run_lock, save_state

    with run_lock(target):
        state = load_state(target)
        state["device_before"] = value
        save_state(target, state)


def flows_saved(agent_dir: Path) -> bool:
    folder = agent_dir / "media" / "flows"
    return folder.is_dir() and any(path.suffix in {".yaml", ".yml", ".sh"} and not path.name.startswith("_")
                                   for path in folder.iterdir())


def _replay_hint(agent_dir: Path) -> str | None:
    return ("CLI device replay --target TARGET --serial SERIAL (installs the new build and re-runs the saved flows)"
            if flows_saved(agent_dir) else None)


def _keep_review_if_formatting_only(target: Path, agent_dir: Path, state: dict[str, Any], record: bool = True) -> bool:
    """An approved review stays valid when everything since it is whitespace (the gate's formatter)."""
    from android_workflow.cli import append_stage_log, git_lines, run_lock, save_state, snapshot_tree, source_fingerprint

    review = (state.get("stages") or {}).get("T6") or {}
    old = review.get("reviewed_tree")
    if not old:
        return False
    try:
        new = snapshot_tree(target)
    except Exception:  # noqa: BLE001
        return False
    if not new:
        return False
    if new == old:
        changed = []
    else:
        changed = git_lines(target, "diff", "-w", "--ignore-blank-lines", "--name-only", old, new)
        if changed is None:
            return False
    if changed:
        return False
    if not record:
        return True
    with run_lock(target):
        from android_workflow.cli import load_state

        fresh = load_state(target)
        entry = (fresh.get("stages") or {}).get("T6") or {}
        entry.update({"fingerprint": source_fingerprint(target), "reviewed_tree": new, "kept": "formatting-only change"})
        fresh["stages"]["T6"] = entry
        save_state(target, fresh)
        append_stage_log(agent_dir, "T6", "kept", "cli", "approved review kept: only formatting changed since")
    state["stages"]["T6"] = entry
    return True


def _record(target: Path, agent_dir: Path, answer: dict[str, Any]) -> None:
    line = {"at": round(time.time(), 3),
            **{key: answer[key] for key in ("next", "agent", "mode", "why", "fix_of", "ids") if key in answer}}
    try:
        with (agent_dir / DECISIONS_FILE).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(line, ensure_ascii=False) + "\n")
    except OSError:
        pass
    stage = answer.get("stage")
    if answer.get("next") not in {"spawn", "checks", "fix"} or not stage:
        return
    from android_workflow.cli import load_state, log_stage, resolve_stage

    stage_id = resolve_stage(stage)[0]
    current = ((load_state(target).get("stages") or {}).get(stage_id) or {}).get("status")
    if current in {"started", "running"}:
        return
    if answer.get("slices") or answer.get("routes"):
        return  # each slice logs its own start with --slice
    log_stage(target, stage, "started", str(answer.get("note") or answer.get("why") or ""), "cli")
    answer["logged"] = f"{stage} started"
