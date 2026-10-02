"""An engineering team: the Tech Lead splits the plan into slices that several Implementers build at once.

The Tech Lead writes `RUN/team-plan.json`; `team` checks it (every file has one owner, at most
`team.max_parallel` slices) and `team --check` reports, after the Implementers, who touched what, so
the Tech Lead can integrate before the single quality gate. Slices share one checkout: what keeps them
apart is file ownership, not separate worktrees.
"""

from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

from android_workflow.paths import cache_dir, run_dir

PLAN_NAME = "team-plan.json"
REPORT_NAME = "team-report.json"
BASELINE_NAME = "team-baseline.json"
DEFAULT_MAX_PARALLEL = 3
MAX_SLICES = 3  # the office has three Implementer desks


def normalize_slice(value: str) -> str:
    match = re.fullmatch(r"[Ss]?([1-9])", str(value).strip())
    if not match:
        raise ValueError(f"unknown slice {value!r}; use the id from team-plan.json (S1, S2, S3)")
    return f"S{match.group(1)}"


def slice_role(slice_id: str) -> str:
    """S1 is the usual Implementer desk; the others get a numbered desk next to it."""
    number = int(normalize_slice(slice_id)[1:])
    return "Implementer" if number == 1 else f"Implementer {number}"


def read_plan(agent_dir: Path) -> dict[str, Any] | None:
    """The team plan when it is a readable object with a list of slices, else None."""
    path = agent_dir / PLAN_NAME
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(value, dict) or not isinstance(value.get("slices"), list):
        return None
    slices = [item for item in value["slices"] if isinstance(item, dict)]
    return {**value, "slices": slices}


def max_parallel(target: Path) -> int:
    for path in (cache_dir(target) / "project-config.json", target / ".ai" / "android-workflow.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        limit = (value.get("team") or {}).get("max_parallel") if isinstance(value, dict) else None
        if isinstance(limit, int) and 1 <= limit <= MAX_SLICES:
            return limit
    return DEFAULT_MAX_PARALLEL


def _clean_path(raw: Any) -> tuple[str | None, str | None]:
    if not isinstance(raw, str) or not raw.strip():
        return None, "a file entry must be a non-empty path"
    text = raw.strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    path = PurePosixPath(text)
    if path.is_absolute() or re.match(r"^[A-Za-z]:", text):
        return None, f"{raw}: use a path relative to the app"
    if ".." in path.parts:
        return None, f"{raw}: must stay inside the app"
    if ".ai" in path.parts:
        return None, f"{raw}: workflow files are not code"
    return path.as_posix(), None


def validate_plan(plan: dict[str, Any] | None, limit: int) -> list[str]:
    if plan is None:
        return [f"{PLAN_NAME} is missing or is not a JSON object with a `slices` list"]
    slices = plan["slices"]
    errors: list[str] = []
    if not slices:
        errors.append("the plan has no slices")
    if len(slices) > limit:
        errors.append(f"{len(slices)} slices; at most {limit} Implementers work at once (team.max_parallel)")
    owner: dict[str, str] = {}
    for index, item in enumerate(slices):
        expected = f"S{index + 1}"
        if item.get("id") != expected:
            errors.append(f"slice {index + 1} must have id {expected!r} (got {item.get('id')!r})")
        name = expected
        if not str(item.get("goal") or "").strip():
            errors.append(f"{name}: `goal` says what this Implementer builds")
        files = item.get("files")
        if not isinstance(files, list) or not files:
            errors.append(f"{name}: `files` lists every file it creates or edits, tests included")
            continue
        for raw in files:
            path, problem = _clean_path(raw)
            if problem:
                errors.append(f"{name}: {problem}")
                continue
            if path in owner and owner[path] != name:
                errors.append(f"{path} is owned by {owner[path]} and {name}; every file needs one owner")
            owner.setdefault(path, name)
    return errors


def owners(plan: dict[str, Any]) -> dict[str, str]:
    found: dict[str, str] = {}
    for item in plan.get("slices") or []:
        for raw in item.get("files") or []:
            path, problem = _clean_path(raw)
            if path and not problem:
                found.setdefault(path, str(item.get("id")))
    return found


def check_plan(target: Path) -> tuple[dict[str, Any], int]:
    plan = read_plan(run_dir(target))
    limit = max_parallel(target)
    errors = validate_plan(plan, limit)
    if errors:
        return {"status": "invalid", "errors": errors, "max_parallel": limit}, 1
    assert plan is not None
    slices = [
        {"id": item["id"], "role": slice_role(item["id"]), "goal": item.get("goal"),
         "files": [_clean_path(raw)[0] for raw in item["files"]]}
        for item in plan["slices"]
    ]
    _record_baseline(target)
    if len(slices) == 1:
        return {
            "status": "single", "slices": slices,
            "next": "one Implementer: spawn aw-implementer as usual, without --slice",
        }, 0
    return {
        "status": "ready", "parallel": len(slices), "slices": slices,
        "next": "spawn one aw-implementer per slice in the same turn, each with its --slice; "
                "then `team --check` and the Tech Lead in integrate mode",
    }, 0


def _record_baseline(target: Path) -> None:
    """What was already changed before any Implementer started (the developer's own work in progress),
    so the report never calls it unowned. Written once, when the plan is first accepted."""
    from android_workflow.cli import write_json
    from android_workflow.live import live_changes

    path = run_dir(target) / BASELINE_NAME
    if path.exists():
        return
    files = {item["path"]: item.get("mtime") for item in live_changes(target, limit=100_000, full=True)}
    write_json(path, {"schema_version": 1, "files": files})


def team_report(target: Path) -> dict[str, Any]:
    """After the Implementers: which slice owns each changed file, and what nobody owned."""
    from android_workflow.cli import read_json, write_json
    from android_workflow.live import live_changes

    agent_dir = run_dir(target)
    plan = read_plan(agent_dir)
    if plan is None:
        raise ValueError(f"no {PLAN_NAME} in this run; the team was not used")
    owned = owners(plan)
    # Every file from the run's base to the working tree, new files one by one (not their folder).
    items = live_changes(target, limit=100_000, full=True)
    baseline_path = agent_dir / BASELINE_NAME
    baseline = (read_json(baseline_path).get("files") or {}) if baseline_path.exists() else {}
    # Already changed before the team started and not touched since: the developer's, not the team's.
    preexisting = sorted(item["path"] for item in items
                         if item["path"] in baseline and baseline[item["path"]] == item.get("mtime") and item["path"] not in owned)
    changed = sorted(item["path"] for item in items if item["path"] not in preexisting)
    metrics_path = agent_dir / "stage-metrics.json"
    metrics = read_json(metrics_path) if metrics_path.exists() else {}
    reported = ((metrics.get("stages") or {}).get("T4") or {}).get("slices") or {}
    state_path = agent_dir / "run-state.json"
    state = read_json(state_path) if state_path.exists() else {}
    slice_states = (((state.get("stages") or {}).get("T4") or {}).get("slices")) or {}
    out_of_slice = {}
    for slice_id, entry in reported.items():
        foreign = [path for path in (entry.get("files") or []) if owned.get(_clean_path(path)[0] or "") != slice_id]
        if foreign:
            out_of_slice[slice_id] = foreign
    report = {
        "schema_version": 1,
        "slices": [
            {"id": item["id"], "role": slice_role(item["id"]),
             "status": (slice_states.get(item["id"]) or {}).get("status") or "not_started",
             "changed": [path for path in changed if owned.get(path) == item["id"]]}
            for item in plan["slices"]
        ],
        "unowned": [path for path in changed if path not in owned],
        "preexisting": preexisting,
        "out_of_slice": out_of_slice,
    }
    report["status"] = "clean" if not report["unowned"] and not out_of_slice else "needs_integration"
    pending = [item["id"] for item in report["slices"] if item["status"] != "completed"]
    if pending:
        report["status"] = "slices_pending"
        report["pending"] = pending
    write_json(agent_dir / REPORT_NAME, report)
    return report
