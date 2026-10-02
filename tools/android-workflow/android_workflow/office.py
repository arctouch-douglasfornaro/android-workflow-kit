"""The office: an isometric, pixel-art page that shows the agents of the current run at their desks.

Rewritten after every CLI command into ``TARGET/.ai/workflow/office.html`` from the files the run
already writes, so it costs no tokens and needs no server. A browser cannot read sibling files from
a local page, so the readable run files are embedded (size-capped); big Gradle logs are links only.
While the run is going the page loads ``office-data.js`` every two seconds and updates in place (no
reload, an open panel stays open); a small watcher (``live.py``) keeps that file current between
commands, so clocks, file edits and new activity show up as they happen.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from android_workflow.paths import current_ticket_id, list_runs, workflow_root

OFFICE_NAME = "office.html"
DATA_NAME = "office-data.js"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
VIDEO_SUFFIXES = {".mp4", ".webm"}
MAX_EMBED_BYTES = 80_000

# (stage, role, kind, shirt colour): desks in the order the work flows. A team adds the Tech Lead
# and Implementers 2 and 3 (slices S2, S3; the usual Implementer desk is slice S1).
DESKS = (
    ("T0", "Setup", "agent", "#8e6bd8"),
    ("T3", "Planner", "agent", "#3d8bfd"),
    ("T4L", "Tech Lead", "agent", "#c0392b"),
    ("T4", "Implementer", "agent", "#2fb36d"),
    ("T4:S2", "Implementer 2", "agent", "#1f9e8f"),
    ("T4:S3", "Implementer 3", "agent", "#7cb342"),
    ("T5", "Quality gate", "machine", "#9aa4b2"),
    ("T6", "Reviewer", "agent", "#e8913a"),
    ("T7", "Device", "agent", "#e2557b"),
    ("T8", "Delivery", "agent", "#18a5a7"),
)
TEAM_ROLES = {"T4L", "T4:S2", "T4:S3"}
# What each desk opens: (path relative to the run folder, label).
ROLE_FILES = {
    "Orchestrator": (("run-state.json", "Run state"), ("stage-metrics.json", "Time & tokens"),
                     ("stage-log.md", "Stage log")),
    "Setup": (("../../project-profile.md", "Project profile"), ("../../android-workflow.json", "Project overrides")),
    "Planner": (("plan.md", "Plan"), ("ticket-spec.json", "Ticket"), ("change-set-map.json", "Likely files")),
    "Tech Lead": (("team-plan.json", "Team plan"), ("team-report.json", "Who touched what")),
    "Implementer": (("implementation-notes.md", "Notes"), ("skills.json", "Skills"), ("t4-files.json", "Changed files"),
                    ("slices/S1.md", "Slice notes")),
    "Implementer 2": (("slices/S2.md", "Slice notes"),),
    "Implementer 3": (("slices/S3.md", "Slice notes"),),
    "Quality gate": (("gate-report.json", "Gate report"),),
    "Reviewer": (("review.json", "Review"),),
    "Device": (("device-report.md", "Device report"), ("prebuild.json", "Base build")),
    "Delivery": (("pr-description.md", "PR description"), ("delivery.json", "Delivery"),
                 ("commit-message.txt", "Commit message")),
}
WORKING = {"started", "running"}
FAILED = {"escalated", "failed", "blocked"}


def office_path(target: Path) -> Path:
    return workflow_root(target) / OFFICE_NAME


def _alive(pid: Any) -> bool:
    """True unless `pid` names a process that is gone (an unknown pid counts as alive)."""
    if not isinstance(pid, int) or pid <= 0:
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def run_is_live(run: Path) -> bool:
    """A run is live until it ends; after `finish`, also while the Delivery agent is still at work."""
    state = _json(run / "run-state.json")
    if (state.get("status") or "idle") not in {"completed", "idle"}:
        return True
    delivery = (state.get("stages") or {}).get("T8") or {}
    return isinstance(delivery, dict) and str(delivery.get("status") or "") in WORKING


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _timeline(run: Path, limit: int = 60) -> list[dict[str, str]]:
    try:
        lines = (run / "stage-log.md").read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows = []
    for line in lines:
        match = re.match(r"^\| (\S+Z) \| (.+?) \| (.+?) \| (.+?) \| (.*?) \|$", line)
        if match:
            stamp, role, status, _actor, note = match.groups()
            rows.append({"time": stamp[11:19], "role": role, "status": status, "note": note.replace("\\|", "|")})
    return rows[-limit:]


ROLE_ALIASES = {"Triage": "Planner", "Localizer": "Planner", "Bootstrap": "Orchestrator", "Telemetry": "Orchestrator"}


def _work(run: Path, state: dict[str, Any], desks: list[dict[str, Any]], now: float) -> dict[str, list[list[float | None]]]:
    """When each agent was working, as [start, end] epoch intervals (end None = still working).

    Built from `started` and the next event of the same role in the stage log. The office uses it to
    keep working agents at their desk and to let the others follow their routine.
    """
    try:
        lines = (run / "stage-log.md").read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    events: list[tuple[float, str, str]] = []
    for line in lines:
        match = re.match(r"^\| (\S+Z) \| (.+?) \| (.+?) \|", line)
        if not match:
            continue
        try:
            stamp = datetime.strptime(match.group(1), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
        events.append((stamp, ROLE_ALIASES.get(match.group(2), match.group(2)), match.group(3)))
    states = {desk["role"]: desk["state"] for desk in desks}
    work: dict[str, list[list[float | None]]] = {}
    open_at: dict[str, float] = {}
    for stamp, role, status in events:
        if status in WORKING:
            open_at.setdefault(role, stamp)
        elif role in open_at:
            work.setdefault(role, []).append([open_at.pop(role), stamp])
    last = events[-1][0] if events else now
    for role, start in open_at.items():
        work.setdefault(role, []).append([start, None if states.get(role) == "working" else last])
    for role, desk_state in states.items():  # working without a `started` row (an older run)
        if desk_state == "working" and not any(end is None for _, end in work.get(role, [])):
            work.setdefault(role, []).append([float(state.get("updated_at") or now), None])
    created = state.get("created_at")
    if created:  # the Orchestrator coordinates for the whole run
        live = (state.get("status") or "") not in {"completed", "idle"}
        work["Orchestrator"] = [[float(created), None if live else last]]
    return work


def _link(path: Path, page_dir: Path) -> dict[str, Any]:
    """A file as the page sees it: `src` is relative to the folder the page is written in."""
    resolved = path.resolve()
    relative = Path(os.path.relpath(resolved, page_dir.resolve())).as_posix()
    stat = path.stat()
    return {"name": path.name, "src": relative, "uri": resolved.as_uri(), "size": stat.st_size, "mtime": round(stat.st_mtime, 3)}


def _media(run: Path, root: Path) -> dict[str, list[dict[str, Any]]]:
    found: dict[str, list[dict[str, Any]]] = {}
    for phase in ("before", "after"):
        folder = run / "media" / phase
        items = []
        if folder.is_dir():
            for path in sorted(folder.iterdir()):
                suffix = path.suffix.lower()
                if suffix in IMAGE_SUFFIXES or suffix in VIDEO_SUFFIXES:
                    kind = "video" if suffix in VIDEO_SUFFIXES else "image"
                    items.append({**_link(path, root), "name": path.stem, "kind": kind})
        found[phase] = items
    return found


def _artifact(path: Path, label: str, root: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    raw = path.read_bytes()
    text = raw[:MAX_EMBED_BYTES].decode("utf-8", errors="replace")
    kind = {".md": "md", ".json": "json", ".yaml": "yaml", ".yml": "yaml"}.get(path.suffix.lower(), "text")
    return {**_link(path, root), "label": label, "kind": kind, "content": text, "truncated": len(raw) > MAX_EMBED_BYTES}


def _artifacts(run: Path, root: Path) -> dict[str, list[dict[str, Any]]]:
    found: dict[str, list[dict[str, Any]]] = {}
    for role, files in ROLE_FILES.items():
        found[role] = [item for item in (_artifact(run / rel, label, root) for rel, label in files) if item]
    flows = run / "media" / "flows"
    if flows.is_dir():
        found["Device"].extend(
            item for item in (_artifact(path, f"Flow · {path.stem}", root) for path in sorted(flows.glob("*.y*ml"))) if item
        )
    return found


def _desk_state(stage: str, entry: dict[str, Any], route: list[str], gate: dict[str, Any], review: dict[str, Any]) -> str:
    status = str(entry.get("status") or "")
    if stage == "T5" and gate.get("status") in {"passed", "failed", "blocked"} and status not in WORKING:
        return {"passed": "done", "failed": "failed", "blocked": "failed"}[gate["status"]]
    if status in WORKING:
        return "working"
    if status in FAILED:
        return "failed"
    if status == "skipped" or (not status and stage not in route and stage != "T0"):
        return "skipped"
    if status == "completed":
        if stage == "T6" and (review.get("status") != "approved" or review.get("blocking")):
            return "failed"
        if stage == "T7" and str(entry.get("verdict") or "pass") not in {"pass", "passed"}:
            return "failed"
        return "done"
    if status == "awaiting_host":
        return "waiting"
    return "idle"


def _history(target: Path, page_dir: Path) -> list[dict[str, Any]]:
    """Every run of the app, newest first, with a link to its own office page."""
    root = workflow_root(target)
    runs = []
    for item in list_runs(target):
        run = Path(item["path"])
        state = _json(run / "run-state.json")
        pr = (_json(run / "delivery.json").get("pr") or {})
        page = root / OFFICE_NAME if item["current"] else run / OFFICE_NAME
        updated = state.get("updated_at") or state.get("created_at")
        runs.append({
            "ticket": item["ticket_id"], "title": item["title"] or "", "status": item["status"] or "unknown",
            "current": item["current"], "href": Path(os.path.relpath(page, page_dir.resolve())).as_posix(),
            "updated": datetime.fromtimestamp(updated, timezone.utc).strftime("%Y-%m-%d %H:%M") if updated else None,
            "updated_at": updated or 0, "pr": pr.get("url") or pr.get("compare_url"),
            "draft": bool((state.get("draft") or {}).get("issues")),
        })
    return sorted(runs, key=lambda run: (not run["current"], -run["updated_at"]))


def _team(run: Path, stages: dict[str, Any]) -> dict[str, Any] | None:
    """The engineering team of this run, when the Tech Lead worked or split the plan into slices."""
    from android_workflow.team import owners, read_plan

    plan = read_plan(run)
    slices = [item for item in (plan or {}).get("slices") or [] if isinstance(item.get("id"), str)]
    if len(slices) < 2 and not stages.get("T4L"):
        return None
    return {
        "slices": [{"id": item["id"], "goal": str(item.get("goal") or "")[:200]} for item in slices[:3]],
        "owners": owners(plan) if len(slices) >= 2 else {},
    }


def collect(target: Path, ticket: str | None = None, page_dir: Path | None = None, live_src: str | None = None,
            full_scan: bool = False, snapshot: bool = False) -> dict[str, Any]:
    """The page data for one run (the current one by default), with links relative to `page_dir`.

    `live_src` is the data script the page reloads while the run is live (the main page only).
    """
    root = workflow_root(target)
    current = current_ticket_id(target)
    ticket = ticket or current
    page_dir = (page_dir or root).resolve()
    now = time.time()
    data: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "ticket": ticket,
        "is_current": ticket == current,
        "history": _history(target, page_dir),
        "live": False,
        "live_src": live_src,
    }
    if not ticket:
        data.update({"status": "idle", "desks": [], "timeline": [], "media": {"before": [], "after": []},
                     "artifacts": {}, "logs": [], "files": [], "draft": [], "totals": {}, "changes": []})
        return data
    run = root / ticket
    state = _json(run / "run-state.json")
    spec = _json(run / "ticket-spec.json")
    metrics = _json(run / "stage-metrics.json")
    gate = _json(run / "gate-report.json")
    review = _json(run / "review.json")
    delivery = _json(run / "delivery.json")
    stages = state.get("stages") or {}
    measured_stages = metrics.get("stages") or {}
    route = list(spec.get("route") or [])
    team = _team(run, stages)
    team_slices = {item["id"] for item in (team or {}).get("slices") or []}
    desks = []
    for stage, role, kind, colour in DESKS:
        if stage in TEAM_ROLES and (not team or (stage.startswith("T4:") and stage[3:] not in team_slices)):
            continue
        if stage.startswith("T4:") or (stage == "T4" and len(team_slices) >= 2):
            # One Implementer of the team: its slice has its own state, clock and tokens.
            slice_id = stage[3:] if stage.startswith("T4:") else "S1"
            entry = (((stages.get("T4") or {}).get("slices") or {}).get(slice_id)) or {}
            measured = (((measured_stages.get("T4") or {}).get("slices") or {}).get(slice_id)) or {}
            state_name = _desk_state("T4", entry, route + ["T4"], gate, review)
        else:
            entry = stages.get(stage) or {}
            measured = measured_stages.get(stage) or {}
            state_name = _desk_state(stage, entry, route + (["T4L"] if team else []), gate, review)
        note = entry.get("note") or entry.get("reason") or ""
        if stage == "T5" and state_name == "working" and not _alive(entry.get("pid")):
            # The gate was killed (a host's command timeout): it is not running any more.
            state_name, note = "failed", "the gate stopped before it finished"
        if stage == "T5" and gate.get("status") and gate["status"] != "not_run":
            note = note or f"gate {gate['status']}"
        since = measured.get("running_since")
        desks.append({
            "stage": stage, "role": role, "kind": kind, "colour": colour,
            "state": state_name,
            "note": str(note)[:200],
            "seconds": measured.get("wall_time_seconds"),
            "tokens": measured.get("tokens"),
            "running_since": since if state_name == "working" and isinstance(since, (int, float)) else None,
        })
    final_totals = dict(metrics.get("totals") or {})
    totals = final_totals
    if not totals:  # mid-run: add up what the stages reported so far
        reported = [item.get("tokens") for item in measured_stages.values()
                    if isinstance(item, dict) and isinstance(item.get("tokens"), int)]
        created = state.get("created_at")
        totals = {
            "wall_time_seconds": max(now - created, 0) if created else None,
            "tokens": sum(reported) if reported else None,
        }
    pr = delivery.get("pr") or {}
    draft = state.get("draft") or {}
    # A run's own page (reached from the history) is a snapshot: only the main page follows the run live.
    live = ticket == current and run_is_live(run) and not snapshot
    changes: list[dict[str, Any]] = []
    if live:
        from android_workflow.live import live_changes

        changes = live_changes(target, full=full_scan)
    data.update({
        "title": (spec.get("ticket") or {}).get("title") or "",
        "status": state.get("status") or "idle",
        "current": state.get("current_stage"),
        "question": (state.get("pending_question") or {}).get("question"),
        "draft": draft.get("issues") or [],
        "pr": pr.get("url") or pr.get("compare_url"),
        "totals": totals,
        "totals_final": bool(final_totals),
        "created_at": state.get("created_at"),
        "desks": desks,
        "team": team,
        "changes": changes,
        "timeline": _timeline(run),
        "media": _media(run, page_dir),
        "artifacts": _artifacts(run, page_dir),
        "logs": [_link(path, page_dir) for path in sorted(run.glob("gate-run*.log"))],
        "files": [{**_link(path, page_dir), "rel": path.relative_to(run).as_posix()} for path in sorted(run.rglob("*"))
                  if path.is_file() and "media" not in path.relative_to(run).parts
                  and path.name not in {OFFICE_NAME, DATA_NAME, f"{Path(OFFICE_NAME).stem}.tmp"}
                  and not path.name.endswith(".tmp") and not path.name.startswith(".")],
        "work": _work(run, state, desks, now),
        # Only the run that is going right now keeps updating; a past run's page is a snapshot.
        "live": live,
    })
    return data


def _payload(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/")


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _write(path: Path, data: dict[str, Any]) -> None:
    _atomic_write(path, PAGE.replace("__SIM__", SIM_JS).replace("__DATA__", _payload(data)))


def _write_data(path: Path, data: dict[str, Any]) -> None:
    _atomic_write(path, f"window.officeData && window.officeData({_payload(data)});\n")


def _stale(page: Path, run: Path) -> bool:
    if not page.exists():
        return True
    built = page.stat().st_mtime
    return any(item.stat().st_mtime > built for item in run.iterdir() if item.is_file() and item != page)


def render(target: Path) -> Path:
    """Write the office page for TARGET's current run, plus one page per run for the history."""
    current = current_ticket_id(target)
    main = office_path(target)
    data = collect(target, current, main.parent, live_src=DATA_NAME)
    _write(main, data)
    _write_data(main.parent / DATA_NAME, data)
    for item in list_runs(target):
        run = Path(item["path"])
        page = run / OFFICE_NAME
        if item["ticket_id"] == current or _stale(page, run):
            _write(page, collect(target, item["ticket_id"], run, snapshot=True))
    return main


def write_live_data(target: Path, last_key: str | None = None, full_scan: bool = False) -> tuple[str, bool]:
    """The watcher's tick: rewrite the page's data script only when something changed."""
    main = office_path(target)
    data = collect(target, current_ticket_id(target), main.parent, live_src=DATA_NAME, full_scan=full_scan)
    stable = {k: v for k, v in data.items() if k != "generated_at"}
    if not data.get("totals_final") and isinstance(stable.get("totals"), dict):
        # Mid-run the total is "now minus the start": it changes every tick but the page counts it itself.
        stable["totals"] = {**stable["totals"], "wall_time_seconds": None}
    key = json.dumps(stable, sort_keys=True, ensure_ascii=False)
    if key != last_key:
        _write_data(main.parent / DATA_NAME, data)
    return key, bool(data.get("live"))


SIM_JS = r"""/* Pure office model: plan, furniture, points of interest, walkable cells and paths. No DOM.
   `SIM` is the usual office; `SIM.build(roster)` builds the same office for another set of agents
   (an engineering team adds the Tech Lead and more Implementers, each with a desk). */
const SIM = (() => {
  const W = 17, D = 10, CELL = 0.5;
  const COLS = Math.round(W / CELL), ROWS = Math.round(D / CELL);
  // Desks: [x, y, width]; the agent sits on a chair just behind the desk (smaller y), facing the viewer.
  const LAYOUT = { Orchestrator: [5.6, 1.4, 2.4], "Tech Lead": [2.3, 1.4, 2], "Implementer 3": [8.6, 1.4, 1.8],
    Setup: [1.0, 3.9, 2], Planner: [4.4, 3.9, 2], Implementer: [7.8, 3.9, 2], "Implementer 2": [9.8, 3.9, 1.8],
    "Quality gate": [0.8, 7.4, 2], Reviewer: [3.2, 7.6, 2], Device: [6.1, 7.6, 2], Delivery: [9.0, 7.6, 2] };
  const BASE = ["Orchestrator", "Setup", "Planner", "Implementer", "Reviewer", "Device", "Delivery"];
  const ORDER = ["Orchestrator", "Setup", "Planner", "Tech Lead", "Implementer", "Implementer 2", "Implementer 3", "Reviewer", "Device", "Delivery"];
  const p = (x, y, face, pose) => ({ x, y, face, pose: pose || { x, y } });
  // Where agents go when idle. `at` is where they stand to arrive; `pose` is where they are drawn.
  const POIS = {
    coffee: [p(13.6, 1.15, "-y"), p(14.4, 1.15, "-y")],
    copa: [p(14.77, 2.97, "+y"), p(14.77, 4.77, "-y"), p(13.87, 3.87, "+x"), p(15.72, 3.87, "-x")],
    sofa: [p(14.1, 7.1, "-y", { x: 14.1, y: 7.88 }), p(15.3, 7.1, "-y", { x: 15.3, y: 7.88 })],
    chat: [[p(12.5, 4.4, "+y"), p(12.5, 5.4, "-y")], [p(12.5, 7.6, "+y"), p(12.5, 8.6, "-y")], [p(6.1, 6.1, "+x"), p(7.1, 6.1, "-x")]],
    window: [p(2.3, 0.75, "-y"), p(10.6, 0.75, "-y")],
    cooler: [p(12.35, 3.1, "-y")],
  };
  const SLOT = 24, SPEED = 2.2;  // seconds per routine slot; tiles walked per second
  const ACTIVITIES = [["coffee", 3], ["copa", 2], ["sofa", 2], ["chat", 3], ["window", 1], ["cooler", 1], ["game", 2], ["desk", 2]];
  const TOTAL = ACTIVITIES.reduce((sum, [, w]) => sum + w, 0);
  function hash(text) {  // FNV-1a, 32 bit
    let h = 0x811c9dc5;
    for (let i = 0; i < text.length; i++) { h ^= text.charCodeAt(i); h = Math.imul(h, 0x01000193) >>> 0; }
    return h >>> 0;
  }
  const rand = (...parts) => hash(parts.join("|")) / 4294967296;

  function build(roster) {
    const AGENTS = ORDER.filter(role => (roster || BASE).includes(role));
    const FURNITURE = [];
    const add = item => { FURNITURE.push(item); return item; };
    for (const [role, [x, y, w]] of Object.entries(LAYOUT)) {
      if (role === "Quality gate") { add({ id: "gate", kind: "machine", role, x: x + 0.3, y: y - 0.2, w: 1.1, d: 0.9, h: 54 }); continue; }
      if (!AGENTS.includes(role)) continue;
      add({ id: `desk:${role}`, kind: "desk", role, x, y, w, d: 0.9, h: 17 });
      add({ id: `chair:${role}`, kind: "chair", role, x: x + w / 2 - 0.25, y: y - 0.82, w: 0.5, d: 0.52, h: 28, walkable: true });
    }
    add({ id: "plant:1", kind: "plant", x: 0.3, y: 0.3, w: 0.45, d: 0.45, h: 40 });
    add({ id: "plant:2", kind: "plant", x: 11.05, y: 0.3, w: 0.45, d: 0.45, h: 40 });
    add({ id: "plant:3", kind: "plant", x: 0.3, y: 9.25, w: 0.45, d: 0.45, h: 40 });
    add({ id: "plant:4", kind: "plant", x: 16.35, y: 9.25, w: 0.45, d: 0.45, h: 40 });
    add({ id: "cooler", kind: "cooler", x: 12.1, y: 2.2, w: 0.5, d: 0.5, h: 44 });
    add({ id: "counter", kind: "counter", x: 13.1, y: 0.05, w: 2.6, d: 0.65, h: 22 });
    add({ id: "fridge", kind: "fridge", x: 16.0, y: 0.1, w: 0.85, d: 0.8, h: 54 });
    add({ id: "table", kind: "table", x: 14.2, y: 3.4, w: 1.2, d: 1.0, h: 16 });
    for (const [id, x, y] of [["N", 14.55, 2.75], ["S", 14.55, 4.55], ["W", 13.65, 3.65], ["E", 15.5, 3.65]]) {
      add({ id: `stool:${id}`, kind: "stool", x, y, w: 0.45, d: 0.45, h: 10, walkable: true });
    }
    add({ id: "tv", kind: "tv", x: 13.7, y: 5.95, w: 2.0, d: 0.5, h: 46 });
    add({ id: "sofa-arm-l", kind: "sofa-arm", x: 13.25, y: 7.6, w: 0.15, d: 0.75, h: 15 });
    add({ id: "sofa-seat", kind: "sofa-seat", x: 13.4, y: 7.6, w: 2.6, d: 0.53, h: 10 });
    add({ id: "sofa-back", kind: "sofa-back", x: 13.4, y: 8.13, w: 2.6, d: 0.22, h: 24 });
    add({ id: "sofa-arm-r", kind: "sofa-arm", x: 16.0, y: 7.6, w: 0.15, d: 0.75, h: 15 });
    const SEATS = {};
    for (const role of AGENTS) {
      const [x, y, w] = LAYOUT[role];
      SEATS[role] = p(x + w / 2, y - 0.55, "+y");
    }
    // Walkable cells: inside the room and not covered by solid furniture (chairs and stools can be sat on).
    const blocked = new Uint8Array(COLS * ROWS);
    for (let c = 0; c < COLS; c++) for (let r = 0; r < ROWS; r++) {
      const x0 = c * CELL, y0 = r * CELL;
      for (const f of FURNITURE) {
        if (f.walkable) continue;
        const ox = Math.min(x0 + CELL, f.x + f.w) - Math.max(x0, f.x), oy = Math.min(y0 + CELL, f.y + f.d) - Math.max(y0, f.y);
        if (ox > 0.02 && oy > 0.02 && ox * oy > 0.04) { blocked[r * COLS + c] = 1; break; }
      }
    }
    const cellOf = (x, y) => [Math.min(COLS - 1, Math.max(0, Math.floor(x / CELL))), Math.min(ROWS - 1, Math.max(0, Math.floor(y / CELL)))];
    const centre = (c, r) => ({ x: (c + 0.5) * CELL, y: (r + 0.5) * CELL });
    const walkable = (x, y) => { const [c, r] = cellOf(x, y); return x >= 0 && y >= 0 && x < W && y < D && !blocked[r * COLS + c]; };
    // Shortest walk between two points over the cell grid (4 directions, the isometric axes). Deterministic.
    const routes = new Map();
    function path(from, to) {
      const [sc, sr] = cellOf(from.x, from.y), [gc, gr] = cellOf(to.x, to.y);
      const start = sr * COLS + sc, goal = gr * COLS + gc;
      if (start === goal) return [{ x: from.x, y: from.y }, { x: to.x, y: to.y }];
      const cached = routes.get(start * 100000 + goal);
      if (cached !== undefined) return cached && [{ x: from.x, y: from.y }, ...cached, { x: to.x, y: to.y }];
      const prev = new Int32Array(COLS * ROWS).fill(-1), queue = [start];
      prev[start] = start;
      for (let i = 0; i < queue.length && prev[goal] < 0; i++) {
        const cur = queue[i], c = cur % COLS, r = (cur - c) / COLS;
        for (const [dc, dr] of [[1, 0], [0, 1], [-1, 0], [0, -1]]) {
          const nc = c + dc, nr = r + dr, n = nr * COLS + nc;
          if (nc < 0 || nr < 0 || nc >= COLS || nr >= ROWS || prev[n] >= 0) continue;
          if (blocked[n] && n !== goal) continue;
          prev[n] = cur; queue.push(n);
        }
      }
      if (prev[goal] < 0) { routes.set(start * 100000 + goal, null); return null; }
      const cells = [];
      for (let n = goal; n !== start; n = prev[n]) cells.push(n);
      cells.reverse();
      const middle = cells.slice(0, -1).map(n => { const c = n % COLS; return centre(c, (n - c) / COLS); });
      if (routes.size > 4000) routes.clear();
      routes.set(start * 100000 + goal, middle);
      return [{ x: from.x, y: from.y }, ...middle, { x: to.x, y: to.y }];
    }
    const pathLength = pts => pts.slice(1).reduce((sum, q, i) => sum + Math.abs(q.x - pts[i].x) + Math.abs(q.y - pts[i].y), 0);

    /* ---- the routine: deterministic, a pure function of (seed, time, work intervals) ---- */
    const working = (work, role, t) => (work[role] || []).some(([s, e]) => s <= t && t < (e == null ? Infinity : e));
    const workedDuring = (work, role, a, b) => (work[role] || []).some(([s, e]) => s < b && a < (e == null ? Infinity : e));
    const seatSpot = role => ({ ...SEATS[role], activity: "desk" });
    const planCache = new Map();
    // Who does what in one slot. Depends only on the seed, the slot and who is idle at its start.
    function plan(seed, slot, work) {
      const idle = AGENTS.filter(role => !working(work, role, slot * SLOT));
      const key = `${seed}|${slot}|${idle.join(",")}`;
      if (planCache.has(key)) return planCache.get(key);
      const order = idle.slice().sort((a, b) => hash(`${seed}|${slot}|${a}`) - hash(`${seed}|${slot}|${b}`) || (a < b ? -1 : 1));
      const free = { coffee: POIS.coffee.slice(), copa: POIS.copa.slice(), sofa: POIS.sofa.slice(), window: POIS.window.slice(),
        cooler: POIS.cooler.slice(), chat: POIS.chat.slice() };
      const out = {};
      let waiting = null;
      for (const role of order) {
        let roll = rand(seed, slot, role, "activity") * TOTAL, activity = "desk";
        for (const [name, weight] of ACTIVITIES) { if (roll < weight) { activity = name; break; } roll -= weight; }
        if (activity === "chat") {
          if (waiting && free.chat.length) {
            const [a, b] = free.chat.shift();
            out[waiting] = { ...a, activity: "chat", partner: role };
            out[role] = { ...b, activity: "chat", partner: waiting };
            waiting = null;
          } else if (!waiting && free.chat.length) waiting = role;
          else out[role] = seatSpot(role);
          continue;
        }
        if (activity === "game" || activity === "desk") { out[role] = { ...seatSpot(role), activity }; continue; }
        out[role] = free[activity].length ? { ...free[activity].shift(), activity } : seatSpot(role);
      }
      if (waiting) out[waiting] = free.coffee.length ? { ...free.coffee.shift(), activity: "coffee" } : seatSpot(waiting);
      if (planCache.size > 500) planCache.clear();
      planCache.set(key, out);
      return out;
    }
    // Where an agent stands at the end of slot k: at the desk if it worked at all in that slot.
    function slotEnd(seed, role, k, work) {
      if (workedDuring(work, role, k * SLOT, (k + 1) * SLOT)) return seatSpot(role);
      return plan(seed, k, work)[role] || seatSpot(role);
    }
    function along(points, distance) {
      for (let i = 1; i < points.length; i++) {
        const a = points[i - 1], b = points[i], len = Math.abs(b.x - a.x) + Math.abs(b.y - a.y);
        if (distance <= len || i === points.length - 1) {
          const f = len ? Math.min(1, distance / len) : 1;
          const face = b.x > a.x ? "+x" : b.x < a.x ? "-x" : b.y > a.y ? "+y" : "-y";
          return { x: a.x + (b.x - a.x) * f, y: a.y + (b.y - a.y) * f, face };
        }
        distance -= len;
      }
      return { ...points[points.length - 1], face: "+y" };
    }
    // From where the agent is drawn now to where it will be drawn on arrival (`to.pose`), over walkable cells.
    function walk(from, to, elapsed) {
      const pose = to.pose || to, route = (path(from, to) || [{ x: from.x, y: from.y }, { x: to.x, y: to.y }]).slice();
      if (pose.x !== to.x || pose.y !== to.y) route.push({ x: pose.x, y: pose.y });
      const length = pathLength(route), duration = length / SPEED;
      if (elapsed >= duration) return null;
      const at = along(route, elapsed * SPEED);
      return { mode: "walk", x: at.x, y: at.y, face: at.face, step: Math.floor(elapsed * SPEED * 2) % 2 };
    }
    const arrived = spot => ({ mode: spot.activity, x: spot.pose.x, y: spot.pose.y, face: spot.face, partner: spot.partner || null });
    // Idle logic only: where an idle agent is at time t (used for itself and as the start of a walk to work).
    function idleState(seed, role, t, work) {
      const k = Math.floor(t / SLOT), start = k * SLOT;
      if (workedDuring(work, role, start, t)) return { ...arrived(seatSpot(role)), mode: "desk" };
      const from = slotEnd(seed, role, k - 1, work), to = plan(seed, k, work)[role] || seatSpot(role);
      return walk(from.pose || from, to, t - start) || arrived(to);
    }
    // The full answer for one agent at time t: working agents walk back and sit at their desk.
    function stateAt(seed, role, t, work) {
      const interval = (work[role] || []).find(([s, e]) => s <= t && t < (e == null ? Infinity : e));
      if (!interval) return { ...idleState(seed, role, t, work), working: false };
      const begin = interval[0], from = idleState(seed, role, begin, work);
      const moving = walk({ x: from.x, y: from.y }, seatSpot(role), t - begin);
      return moving ? { ...moving, working: true } : { ...arrived(seatSpot(role)), mode: "desk", working: true };
    }
    return { W, D, CELL, COLS, ROWS, LAYOUT, AGENTS, FURNITURE, POIS, SEATS, blocked, cellOf, walkable, path, pathLength,
      SLOT, SPEED, hash, plan, stateAt, build, ORDER, BASE };
  }
  return build(BASE);
})();
if (typeof module !== "undefined") module.exports = { SIM };
"""

PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Agent Office</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Press+Start+2P&family=Inter:wght@400;600;700&display=swap" rel="stylesheet">
<style>
:root {
  --bg: #15161f; --panel: #1d1f2b; --panel-2: #252838; --line: #343850; --ink: #eef0f6; --muted: #9aa1b8;
  --ok: #4fd07d; --bad: #ff6262; --warn: #ffc94d; --info: #6cb7ff; --accent: #ffc94d;
  --pixel: "Press Start 2P", ui-monospace, Menlo, monospace;
  --sans: Inter, -apple-system, "Segoe UI", Roboto, sans-serif;
}
* { box-sizing: border-box; }
[hidden] { display: none !important; }
html, body { margin: 0; background: var(--bg); color: var(--ink); font-family: var(--sans); font-size: 14px; }
button { font: inherit; color: inherit; }
a { color: var(--info); }
code { font-family: ui-monospace, Menlo, monospace; }
.app { display: grid; grid-template-columns: minmax(0, 1fr) 400px; height: 100vh; overflow: hidden; }
@media (max-width: 1000px) { .app { grid-template-columns: 1fr; height: auto; overflow: visible; } }
.stage { display: flex; flex-direction: column; min-width: 0; min-height: 0; }
.topbar { display: flex; flex-wrap: wrap; align-items: center; gap: 10px 16px; padding: 14px 18px;
  border-bottom: 1px solid var(--line); background: var(--panel); }
.brand { font-family: var(--pixel); font-size: 12px; letter-spacing: .5px; }
.ticket-title { color: var(--muted); font-size: 13px; overflow-wrap: anywhere; }
.grow { flex: 1; min-width: 0; }
.picker { display: inline-flex; align-items: center; gap: 8px; color: var(--muted); font-size: 12px; }
.picker select { background: var(--panel-2); color: var(--ink); border: 1px solid var(--line); border-radius: 8px;
  padding: 6px 8px; font: inherit; font-size: 13px; max-width: 260px; }
.pastbar { padding: 10px 18px; background: rgba(108, 183, 255, .12); border-bottom: 1px solid #3b6ea5; font-size: 13px; }
.pastbar a { font-weight: 600; }
.chip { display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px; border-radius: 999px;
  background: var(--panel-2); border: 1px solid var(--line); font-size: 12px; white-space: nowrap; }
.chip.running, .chip.awaiting_host, .chip.working, .chip.started { color: #0b1020; background: var(--warn); border-color: transparent; }
.chip.completed, .chip.done, .chip.passed, .chip.approved, .chip.pass { color: #062012; background: var(--ok); border-color: transparent; }
.chip.escalated, .chip.failed, .chip.blocked, .chip.changes_requested, .chip.fail, .chip.needs { color: #fff; background: #c53b3b; border-color: transparent; }
.chip.paused, .chip.waiting, .chip.draft, .chip.waived { color: #1a1300; background: #e2a92b; border-color: transparent; }
.chip.skipped, .chip.not_run, .chip.idle, .chip.not { color: var(--muted); }
.room-wrap { flex: 1; min-height: 0; display: flex; align-items: center; justify-content: center; padding: 12px;
  background: radial-gradient(ellipse at 50% 40%, #232637 0%, #15161f 70%); min-height: 380px; }
.room { width: 100%; height: 100%; max-width: 1100px; display: block; }
@media (max-width: 1000px) { .room { height: auto; } }
@media (max-width: 700px) { .room-wrap { min-height: 0; padding: 4px; } .hint { padding-top: 6px; } }
.room text { font-family: var(--pixel); }
.spot { cursor: pointer; outline: none; }
.spot .hit { fill: transparent; }
.ring { fill: transparent; stroke: transparent; }
.ring.hover { fill: rgba(255, 201, 77, .10); stroke: var(--accent); stroke-width: 2; stroke-dasharray: 6 4; }
.typing .arm { animation: type .32s steps(2) infinite; }
.typing .arm.r { animation-delay: .16s; }
@keyframes type { 50% { transform: translateY(-1.5px); } }
.screen-on { animation: glow 1.2s steps(2) infinite; }
@keyframes glow { 50% { opacity: .65; } }
.zz { animation: float 2.4s steps(4) infinite; }
@keyframes float { 50% { transform: translateY(-3px); } }
.lamp-on { animation: blink .7s steps(2) infinite; }
@keyframes blink { 50% { opacity: .25; } }
.hint { text-align: center; color: var(--muted); font-size: 12px; padding: 0 12px 14px; }
aside.side { background: var(--panel); border-left: 1px solid var(--line); display: flex; flex-direction: column; min-width: 0; min-height: 0; height: 100vh; }
@media (max-width: 1000px) { aside.side { border-left: 0; border-top: 1px solid var(--line); height: auto; } }
.section { padding: 16px 18px; border-bottom: 1px solid var(--line); }
.section h2 { margin: 0 0 10px; font-size: 11px; letter-spacing: 1px; text-transform: uppercase; color: var(--muted); font-weight: 700; }
.stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
.stat { background: var(--panel-2); border: 1px solid var(--line); border-radius: 10px; padding: 10px; min-width: 0; }
.stat b { display: block; font-size: 17px; margin-top: 4px; }
.stat span { color: var(--muted); font-size: 11px; }
.alert { margin-top: 10px; padding: 10px 12px; border-radius: 10px; font-size: 13px; line-height: 1.45; overflow-wrap: anywhere; }
.alert.ok { background: rgba(79, 208, 125, .12); border: 1px solid #4fd07d; }
.alert.warn { background: rgba(226, 169, 43, .15); border: 1px solid #e2a92b; }
.alert.bad { background: rgba(197, 59, 59, .15); border: 1px solid #c53b3b; }
.alert ul { margin: 6px 0 0; padding-left: 18px; }
.files { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.filebtn { display: flex; align-items: center; gap: 10px; text-align: left; padding: 10px; border-radius: 10px;
  border: 1px solid var(--line); background: var(--panel-2); cursor: pointer; min-width: 0; }
.filebtn:hover, .filebtn:focus-visible { border-color: var(--accent); outline: none; }
.filebtn.fresh { border-color: var(--warn); box-shadow: 0 0 0 1px var(--warn) inset; animation: freshglow 1s ease-in-out infinite; }
@keyframes freshglow { 50% { box-shadow: 0 0 0 1px rgba(255, 201, 77, .25) inset; } }
.pulse { animation: pulse .9s ease-in-out infinite; }
@keyframes pulse { 50% { opacity: .45; } }
.livedot { display: inline-block; width: 7px; height: 7px; border-radius: 50%; background: var(--ok); margin-right: 5px; vertical-align: middle; animation: pulse 1.6s ease-in-out infinite; }
.change { display: grid; grid-template-columns: 48px minmax(0, 1fr) auto auto; gap: 4px 10px; align-items: center; padding: 7px 0; border-bottom: 1px dashed var(--line); font-size: 13px; }
.change code { overflow-wrap: anywhere; }
.change .chip { justify-content: center; }
.change .plus { color: var(--ok); font-variant-numeric: tabular-nums; } .change .minus { color: var(--bad); font-variant-numeric: tabular-nums; }
.change .ago { color: var(--muted); font-size: 11px; white-space: nowrap; }
.change.fresh code { color: var(--warn); }
.filebtn .who { font-weight: 600; font-size: 13px; }
.filebtn .what { color: var(--muted); font-size: 11px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.filebtn > div { min-width: 0; }
.avatar { flex: none; width: 28px; height: 28px; border-radius: 8px; display: grid; place-items: center;
  font-family: var(--pixel); font-size: 10px; color: #0b1020; position: relative; }
.avatar .dot { position: absolute; right: -3px; bottom: -3px; width: 10px; height: 10px; border-radius: 50%;
  border: 2px solid var(--panel-2); background: #666; }
.dot.working { background: var(--warn); } .dot.done { background: var(--ok); } .dot.failed { background: var(--bad); }
.dot.waiting { background: #e2a92b; } .dot.skipped { background: #444; }
.feed { flex: 1; overflow: auto; padding: 4px 10px 16px; min-height: 120px; }
.msg { display: flex; gap: 10px; padding: 10px 8px; border-radius: 10px; cursor: pointer; }
.msg:hover, .msg:focus-visible { background: var(--panel-2); outline: none; }
.msg .body { min-width: 0; flex: 1; }
.msg .head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; font-size: 12px; }
.msg .head b { font-size: 13px; }
.msg .time { color: var(--muted); font-size: 11px; margin-left: auto; }
.msg .text { margin-top: 4px; color: #d6d9e6; font-size: 13px; line-height: 1.45; overflow-wrap: anywhere; }
.msg .chip { padding: 1px 8px; font-size: 11px; }
.empty { color: var(--muted); font-size: 13px; padding: 8px; }
.foot { color: var(--muted); font-size: 11px; padding: 10px 18px; border-top: 1px solid var(--line); }
.scrim { position: fixed; inset: 0; background: rgba(5, 6, 12, .55); opacity: 0; pointer-events: none; transition: opacity .15s; z-index: 9; }
.scrim.open { opacity: 1; pointer-events: auto; }
.drawer { position: fixed; top: 0; right: 0; height: 100vh; width: min(760px, 100vw); background: var(--panel);
  border-left: 1px solid var(--line); transform: translateX(100%); transition: transform .18s ease-out;
  display: flex; flex-direction: column; z-index: 10; }
.drawer.open { transform: none; }
.instant { transition: none !important; }
.dhead { display: flex; align-items: center; gap: 12px; padding: 16px 18px; border-bottom: 1px solid var(--line); }
.dhead .avatar { width: 40px; height: 40px; font-size: 13px; }
.dhead h3 { margin: 0; font-size: 17px; }
.dhead .sub { color: var(--muted); font-size: 12px; margin-top: 4px; display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.close { margin-left: auto; width: 34px; height: 34px; border-radius: 8px; border: 1px solid var(--line);
  background: var(--panel-2); cursor: pointer; font-size: 18px; flex: none; }
.dnote { padding: 12px 18px; border-bottom: 1px solid var(--line); background: var(--panel-2); font-size: 13px; line-height: 1.5; overflow-wrap: anywhere; }
.tabs { display: flex; gap: 4px; padding: 8px 14px 0; border-bottom: 1px solid var(--line); overflow-x: auto; }
.tab { padding: 9px 12px; border: 0; border-bottom: 2px solid transparent; background: none; cursor: pointer;
  color: var(--muted); white-space: nowrap; font-size: 13px; }
.tab.on { color: var(--ink); border-bottom-color: var(--accent); }
.dbody { flex: 1; overflow: auto; padding: 18px; line-height: 1.55; }
.dfoot { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; padding: 12px 18px; border-top: 1px solid var(--line);
  color: var(--muted); font-size: 12px; }
.btn { display: inline-flex; align-items: center; gap: 6px; padding: 7px 12px; border-radius: 8px; border: 1px solid var(--line);
  background: var(--panel-2); color: var(--ink); text-decoration: none; cursor: pointer; font-size: 12px; }
.btn:hover { border-color: var(--accent); }
.paused { margin-left: auto; }
.md h1, .md h2, .md h3, .md h4 { margin: 18px 0 8px; line-height: 1.3; }
.md h1 { font-size: 20px; } .md h2 { font-size: 16px; } .md h3 { font-size: 14px; } .md h4 { font-size: 13px; }
.md > :first-child { margin-top: 0; }
.md p { margin: 8px 0; } .md ul, .md ol { margin: 6px 0; padding-left: 22px; }
.md code, .kv code, .item code, td code, .card code { background: #11131c; border: 1px solid var(--line); padding: 1px 5px; border-radius: 5px; font-size: 12px; overflow-wrap: anywhere; }
pre { background: #11131c; border: 1px solid var(--line); border-radius: 10px; padding: 12px; overflow: auto; font-size: 12px; line-height: 1.5; }
pre code { background: none; border: 0; padding: 0; }
table { border-collapse: collapse; width: 100%; font-size: 13px; margin: 8px 0; }
th, td { text-align: left; padding: 7px 8px; border-bottom: 1px solid var(--line); vertical-align: top; }
th { color: var(--muted); font-weight: 600; font-size: 12px; }
.md blockquote { margin: 8px 0; padding: 4px 12px; border-left: 3px solid var(--line); color: var(--muted); }
.card { background: var(--panel-2); border: 1px solid var(--line); border-radius: 12px; padding: 12px 14px; margin: 12px 0; }
.card h4 { margin: 0 0 8px; font-size: 13px; }
.item { padding: 8px 0; border-bottom: 1px dashed var(--line); font-size: 13px; overflow-wrap: anywhere; }
.item:last-child { border-bottom: 0; }
.item .loc { color: var(--muted); font-size: 12px; margin-top: 2px; }
.kv { display: grid; grid-template-columns: 140px minmax(0, 1fr); gap: 8px 12px; font-size: 13px; margin-bottom: 8px; }
.kv > span:nth-child(odd) { color: var(--muted); }
details { margin-top: 14px; } summary { cursor: pointer; color: var(--muted); font-size: 12px; }
.j-key { color: #8fd0ff; } .j-str { color: #b9e88a; } .j-num { color: #ffbf7a; } .j-lit { color: #ff8fb3; }
.pair { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 18px; }
.pair figure { margin: 0; }
.pair img, .pair video { width: 100%; max-height: 460px; object-fit: contain; background: #000; border-radius: 8px; border: 1px solid var(--line); }
figcaption { color: var(--muted); font-size: 11px; margin-top: 4px; }
.seg { display: inline-flex; border: 1px solid var(--line); border-radius: 9px; overflow: hidden; margin-bottom: 14px; }
.seg button { border: 0; background: var(--panel-2); padding: 7px 12px; cursor: pointer; font-size: 12px; color: var(--muted); }
.seg button.on { background: var(--accent); color: #1a1300; font-weight: 600; }
.compare { --pos: 50%; position: relative; height: min(520px, 68vh); background: #000; border-radius: 10px;
  border: 1px solid var(--line); overflow: hidden; touch-action: pan-y; user-select: none; cursor: ew-resize; margin-bottom: 6px; }
.compare img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: contain; pointer-events: none; }
.compare .c-before { clip-path: inset(0 calc(100% - var(--pos)) 0 0); }
.compare .c-line { position: absolute; top: 0; bottom: 0; left: var(--pos); width: 2px; margin-left: -1px; background: var(--accent); pointer-events: none; }
.compare .c-knob { position: absolute; top: 50%; left: var(--pos); width: 30px; height: 30px; margin: -15px 0 0 -15px; border-radius: 50%;
  background: var(--accent); color: #1a1300; display: grid; place-items: center; font-size: 13px; font-weight: 700; pointer-events: none; box-shadow: 0 2px 8px rgba(0,0,0,.5); }
.compare input { position: absolute; inset: auto 0 0 0; width: 100%; opacity: 0; height: 100%; margin: 0; cursor: ew-resize; }
.compare:focus-within .c-knob { outline: 3px solid #fff; outline-offset: 2px; }
.compare .c-tag { position: absolute; top: 8px; padding: 2px 8px; border-radius: 6px; background: rgba(0,0,0,.65); font-size: 11px; pointer-events: none; }
.compare .c-tag.l { left: 8px; } .compare .c-tag.r { right: 8px; }
.muted { color: var(--muted); }
</style>
</head>
<body>
<div class="app">
  <section class="stage">
    <div class="topbar">
      <div class="brand">AGENT OFFICE</div>
      <div class="grow"><b id="ticket"></b> <span class="ticket-title" id="ticketTitle"></span></div>
      <label class="picker" id="pickerWrap" hidden><span>Ticket</span><select id="runPicker" aria-label="Open another run"></select></label>
      <span id="statusChip"></span>
    </div>
    <div class="pastbar" id="pastbar" hidden></div>
    <div class="room-wrap"><svg class="room" id="room" role="img" aria-label="The agents at their desks"></svg></div>
    <div class="hint">Click a desk, a file card or a message to read what that agent produced.</div>
  </section>
  <aside class="side">
    <div class="section" id="summary"></div>
    <div class="section"><h2>Workflow files</h2><div class="files" id="files"></div></div>
    <div class="section" style="padding-bottom:4px;border-bottom:0"><h2>Activity</h2></div>
    <div class="feed" id="feed"></div>
    <div class="foot" id="foot"></div>
  </aside>
</div>
<div class="scrim" id="scrim"></div>
<aside class="drawer" id="drawer" aria-hidden="true" role="dialog" aria-label="Agent details">
  <div class="dhead" id="dhead"></div>
  <div class="dnote" id="dnote"></div>
  <div class="tabs" id="tabs" role="tablist"></div>
  <div class="dbody" id="dbody"></div>
  <div class="dfoot" id="dfoot"></div>
</aside>
<script>
let DATA = __DATA__;
__SIM__
const ROLE_OF_STAGE = { T0: "Setup", T1: "Planner", T2: "Planner", T3: "Planner", T4: "Implementer", T4L: "Tech Lead",
  T5: "Quality gate", T6: "Reviewer", T7: "Device", T8: "Delivery", T9: "Orchestrator" };
const ROLE_ALIAS = { Triage: "Planner", Localizer: "Planner", Telemetry: "Orchestrator", Bootstrap: "Orchestrator" };
const LOOK = { Orchestrator: ["#2a1d14", "#f1c27d"], Setup: ["#c24d2c", "#e0ac69"], Planner: ["#1d1d1d", "#f6d1b0"],
  Implementer: ["#6b3e1e", "#c68642"], Reviewer: ["#e7c35a", "#f6d1b0"], Device: ["#3a2a5a", "#8d5524"], Delivery: ["#111", "#ffdbac"],
  "Tech Lead": ["#4a2c2a", "#e0ac69"], "Implementer 2": ["#d35400", "#f6d1b0"], "Implementer 3": ["#2c3e50", "#a1665e"] };
const STATE_LABEL = { working: "working", done: "done", failed: "needs a fix", skipped: "not needed", waiting: "waiting", idle: "idle" };
// The roles on this page, in the order the work flows (a team adds the Tech Lead and more Implementers).
const roles = () => DATA.ticket ? ["Orchestrator", ...(DATA.desks || []).map(d => d.role)] : [];
const EDITORS = ["Implementer", "Implementer 2", "Implementer 3", "Tech Lead"];

const esc = t => String(t ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const fmtS = v => { if (v == null) return "—"; const s = Math.round(v); return s >= 3600 ? `${Math.floor(s / 3600)}h${String(Math.floor(s % 3600 / 60)).padStart(2, "0")}m` : s >= 60 ? `${Math.floor(s / 60)}m${String(s % 60).padStart(2, "0")}s` : `${s}s`; };
const fmtT = v => v == null ? "—" : v >= 1e6 ? `${(v / 1e6).toFixed(1)}M` : v >= 1000 ? `${(v / 1000).toFixed(1)}k` : String(v);
const fmtB = v => v == null ? "" : v >= 1e6 ? `${(v / 1e6).toFixed(1)} MB` : v >= 1000 ? `${Math.round(v / 1000)} KB` : `${v} B`;
const live = () => Boolean(DATA.live);
// The run waits for the host while its agents work: show that as running.
const runStatus = () => DATA.status === "awaiting_host" && (DATA.desks || []).some(d => d.state === "working") ? "running" : (DATA.status || "idle");
const now = () => Date.now() / 1000;
// Seconds an agent has worked: what was recorded, plus the open stretch while it is working right now.
function liveSeconds(desk) {
  if (!desk) return null;
  let since = desk.running_since;
  if (since == null && desk.state === "working" && live()) {
    const open = ((DATA.work || {})[desk.role] || []).find(([, e]) => e == null);
    if (open) since = open[0];
  }
  if (since == null || !live()) return desk.seconds;
  return (desk.seconds || 0) + Math.max(0, now() - since);
}
const ago = s => s < 5 ? "just now" : s < 60 ? `${Math.floor(s)}s ago` : s < 3600 ? `${Math.floor(s / 60)}m ago` : `${Math.floor(s / 3600)}h ago`;
// Files the change touches, attributed to the agent who owns them (a team splits files by slice).
function changesFor(role) {
  const all = DATA.changes || [], team = DATA.team || null, owners = (team && team.owners) || {};
  const sliceRole = id => id === "S1" ? "Implementer" : `Implementer ${String(id).slice(1)}`;
  const present = new Set((DATA.desks || []).map(d => d.role));
  if (role === "Tech Lead") return all;
  if (!EDITORS.includes(role)) return [];
  return all.filter(c => {
    const owner = owners[c.path] ? sliceRole(owners[c.path]) : (present.has("Tech Lead") && Object.keys(owners).length ? "Tech Lead" : "Implementer");
    return owner === role;
  });
}
function lastTouch(role) {
  let t = 0;
  for (const a of (DATA.artifacts || {})[role] || []) t = Math.max(t, a.mtime || 0);
  if (role !== "Tech Lead") for (const c of changesFor(role)) t = Math.max(t, c.mtime || 0);
  return t;
}
const chip = (s, label) => `<span class="chip ${esc(String(s).toLowerCase().split(" ")[0])}">${esc(label || s)}</span>`;

function bossDesk() {
  const s = DATA.status || "idle";
  const state = s === "completed" ? "done" : s === "escalated" ? "failed" : s === "paused" ? "waiting" : s === "idle" ? "idle" : "working";
  const current = (DATA.desks || []).find(d => d.stage === DATA.current);
  const busy = (DATA.desks || []).filter(d => d.state === "working").map(d => d.role);
  const note = s === "paused" ? `Waiting for your answer: ${DATA.question || ""}`
    : busy.length ? `Coordinating · now ${busy.length > 2 ? `${busy.length} agents` : busy.join(" + ")}`
    : s === "completed" ? (DATA.pr ? ((DATA.draft || []).length ? "Draft PR delivered" : "PR delivered") : "Run finished")
    : current ? `Coordinating · next ${current.role}` : "";
  const running = live() && !DATA.totals_final && DATA.created_at;
  return { stage: "boss", role: "Orchestrator", kind: "agent", colour: "#d4a72c", state, note,
    seconds: running ? 0 : (DATA.totals || {}).wall_time_seconds, running_since: running ? DATA.created_at : null,
    tokens: (DATA.totals || {}).tokens };
}
const desks = () => DATA.ticket ? [bossDesk(), ...(DATA.desks || [])] : [];
const deskOf = role => desks().find(d => d.role === role) || { role, colour: role === "Orchestrator" ? "#d4a72c" : "#888", state: "idle" };

/* ---------- isometric room ---------- */
const TW = 56, TH = 28, W = SIM.W, D = SIM.D, WALL = 112, PAD = 26;
// The office for the agents on this page; rebuilt when a team joins (more desks, other walkways).
const rosterOf = () => roles().filter(r => SIM.ORDER.includes(r));
let OFFICE = SIM.build(rosterOf()), ROSTER_KEY = rosterOf().join();
const OX = D * TW / 2 + PAD, OY = WALL + PAD + 10;
const VW = (W + D) * TW / 2 + PAD * 2, VH = (W + D) * TH / 2 + WALL + PAD * 2 + 20;
const iso = (x, y, z = 0) => [OX + (x - y) * TW / 2, OY + (x + y) * TH / 2 - z];
const pts = a => a.map(p => p.map(n => n.toFixed(1)).join(",")).join(" ");
const poly = (a, fill, extra = "") => `<polygon points="${pts(a)}" fill="${fill}" stroke="rgba(0,0,0,.35)" stroke-width="1" stroke-linejoin="round" ${extra}/>`;
function shade(hex, f) {
  const n = parseInt(hex.slice(1), 16), c = [n >> 16, (n >> 8) & 255, n & 255].map(v => Math.max(0, Math.min(255, Math.round(v * f))));
  return `#${c.map(v => v.toString(16).padStart(2, "0")).join("")}`;
}
function box(x, y, z, w, d, h, color, extra = "") {
  const top = [iso(x, y, z + h), iso(x + w, y, z + h), iso(x + w, y + d, z + h), iso(x, y + d, z + h)];
  const right = [iso(x + w, y, z), iso(x + w, y + d, z), iso(x + w, y + d, z + h), iso(x + w, y, z + h)];
  const front = [iso(x, y + d, z), iso(x + w, y + d, z), iso(x + w, y + d, z + h), iso(x, y + d, z + h)];
  return `<g ${extra}>${poly(front, shade(color, .72))}${poly(right, shade(color, .86))}${poly(top, color)}</g>`;
}
const tile = (x, y, fill, stroke) => `<polygon points="${pts([iso(x, y), iso(x + 1, y), iso(x + 1, y + 1), iso(x, y + 1)])}" fill="${fill}" stroke="${stroke}" stroke-width=".6"/>`;
function floorTiles() {
  let out = "";
  for (let x = 0; x < W; x++) for (let y = 0; y < D; y++) {
    if (x < 12) out += tile(x, y, (x + y) % 2 ? "#c9b896" : "#d6c7a5", "#b9a885");          // office
    else if (x === 12) out += tile(x, y, y % 2 ? "#a77b52" : "#b0845a", "#8e6642");        // corridor planks
    else out += tile(x, y, (x + y) % 2 ? "#cfe3e6" : "#eef6f7", "#b5cdd1");                // copa tiles
  }
  out += poly([iso(5.0, 0.5), iso(9.2, 0.5), iso(9.2, 2.6), iso(5.0, 2.6)], "#7a4e9c", 'opacity=".5"');
  out += poly([iso(13.5, 6.55), iso(16.0, 6.55), iso(16.0, 7.55), iso(13.5, 7.55)], "#d98b5f", 'opacity=".55"');
  return out;
}
function walls() {
  const H = WALL;
  let out = poly([iso(0, 0), iso(W, 0), iso(W, 0, H), iso(0, 0, H)], "#8fa9c8");
  out += poly([iso(12, 0), iso(W, 0), iso(W, 0, H), iso(12, 0, H)], "#9cc5b9");
  out += poly([iso(0, 0), iso(0, D), iso(0, D, H), iso(0, 0, H)], "#7591b4");
  out += poly([iso(0, 0, H), iso(W, 0, H), iso(W, -0.25, H), iso(0, -0.25, H)], "#5b6f8c");
  out += poly([iso(0, 0, H), iso(0, D, H), iso(-0.25, D, H), iso(-0.25, 0, H)], "#4f6280");
  // light falls from the windows: walls brighter at the top, darker near the floor
  out += `<polygon points="${pts([iso(0, 0), iso(W, 0), iso(W, 0, H), iso(0, 0, H)])}" fill="url(#g-wall)"/>`;
  out += `<polygon points="${pts([iso(0, 0), iso(0, D), iso(0, D, H), iso(0, 0, H)])}" fill="url(#g-wall)"/>`;
  out += poly([iso(0, 0, 10), iso(W, 0, 10), iso(W, 0, 0), iso(0, 0, 0)], "#5e4a3a");
  out += poly([iso(0, 0, 10), iso(0, D, 10), iso(0, D, 0), iso(0, 0, 0)], "#4f3e31");
  for (const wx of [1.4, 9.0]) {
    out += poly([iso(wx, 0, 86), iso(wx + 1.8, 0, 86), iso(wx + 1.8, 0, 40), iso(wx, 0, 40)], "#cfe9ff");
    out += poly([iso(wx + 0.88, 0, 86), iso(wx + 0.92, 0, 86), iso(wx + 0.92, 0, 40), iso(wx + 0.88, 0, 40)], "#6d86a8");
    out += poly([iso(wx, 0, 64), iso(wx + 1.8, 0, 64), iso(wx + 1.8, 0, 62), iso(wx, 0, 62)], "#6d86a8");
  }
  out += poly([iso(3.5, 0, 94), iso(5.5, 0, 94), iso(5.5, 0, 58), iso(3.5, 0, 58)], "#2f3d33");
  out += poly([iso(3.6, 0, 92), iso(5.4, 0, 92), iso(5.4, 0, 60), iso(3.6, 0, 60)], "#36513f");
  const [bx, by] = iso(4.5, 0, 74);
  out += `<g transform="matrix(1 0.5 0 1 ${bx} ${by})"><text x="0" y="0" font-size="7.5" fill="#e8f1df" text-anchor="middle">${esc((DATA.ticket || "NO RUN").slice(0, 10))}</text></g>`;
  // copa sign and menu board over the counter
  out += poly([iso(13.3, 0, 96), iso(15.5, 0, 96), iso(15.5, 0, 74), iso(13.3, 0, 74)], "#5b3a29");
  const [sx, sy] = iso(14.4, 0, 82);
  out += `<g transform="matrix(1 0.5 0 1 ${sx} ${sy})"><text x="0" y="0" font-size="6.5" fill="#ffe7b0" text-anchor="middle">COFFEE</text></g>`;
  out += poly([iso(0, 2.2, 88), iso(0, 3.6, 88), iso(0, 3.6, 48), iso(0, 2.2, 48)], "#ffcf5a");
  out += poly([iso(0, 2.45, 80), iso(0, 3.35, 80), iso(0, 3.35, 58), iso(0, 2.45, 58)], "#3d8bfd");
  const [cx, cy] = iso(0, 6, 76);
  out += `<g transform="matrix(1 -0.5 0 1 ${cx} ${cy})"><ellipse cx="0" cy="0" rx="10" ry="10" fill="#f4f1de" stroke="#222" stroke-width="1.5"/><path d="M0 0 V-6 M0 0 H4" stroke="#222" stroke-width="1.4"/></g>`;
  return out;
}
function bubble(x, y, text, bad) {
  const words = String(text || "").slice(0, 48).split(" "), lines = [""];
  for (const w of words) {
    const next = (lines[lines.length - 1] + " " + w).trim();
    if (next.length > 22 && lines[lines.length - 1] && lines.length < 2) lines.push(w); else lines[lines.length - 1] = next;
  }
  const width = Math.max(...lines.map(l => l.length)) * 5.3 + 14, height = lines.length * 9 + 10, fill = bad ? "#ffe1e1" : "#ffffff";
  let out = `<g><rect x="${(x - width / 2).toFixed(1)}" y="${(y - height).toFixed(1)}" width="${width.toFixed(1)}" height="${height}" rx="4" fill="${fill}" stroke="#222" stroke-width="1.2"/>`;
  out += `<polygon points="${x - 4},${y - 0.6} ${x + 4},${y - 0.6} ${x},${y + 5}" fill="${fill}" stroke="#222" stroke-width="1.2"/><rect x="${x - 3.4}" y="${y - 2.2}" width="6.8" height="2.6" fill="${fill}"/>`;
  lines.forEach((l, i) => out += `<text x="${x}" y="${(y - height + 12 + i * 9).toFixed(1)}" font-size="5.6" text-anchor="middle" fill="#111">${esc(l)}</text>`);
  return out + "</g>";
}
function tag(x, y, desk) {
  const width = desk.role.length * 6 + 18;
  const colour = { working: "#ffc94d", done: "#4fd07d", failed: "#ff6262", waiting: "#e2a92b" }[desk.state] || "#9aa1b8";
  return `<g><rect x="${(x - width / 2).toFixed(1)}" y="${y - 12}" width="${width}" height="13" rx="6.5" fill="rgba(10,12,20,.85)"/><circle cx="${(x - width / 2 + 7.5).toFixed(1)}" cy="${y - 5.5}" r="2.7" fill="${colour}"/><text x="${x + 3.5}" y="${y - 3}" font-size="5.8" text-anchor="middle" fill="#fff">${esc(desk.role)}</text></g>`;
}
function statusMark(x, y, desk) {
  if (desk.state === "done") return `<g><circle cx="${x}" cy="${y}" r="7" fill="#4fd07d" stroke="#173" stroke-width="1.2"/><path d="M${x - 3.5} ${y} l2.4 2.6 l4.6 -5" stroke="#fff" stroke-width="1.8" fill="none"/></g>`;
  if (desk.state === "failed") return `<g><circle cx="${x}" cy="${y}" r="7" fill="#ff6262" stroke="#611" stroke-width="1.2"/><rect x="${x - 1}" y="${y - 4.5}" width="2" height="5.5" fill="#fff"/><rect x="${x - 1}" y="${y + 2}" width="2" height="2" fill="#fff"/></g>`;
  if (desk.state === "idle") return `<g class="zz"><text x="${x}" y="${y + 3}" font-size="7" fill="#fff" text-anchor="middle" stroke="#222" stroke-width=".5">z z</text></g>`;
  if (desk.state === "waiting") return `<g class="zz"><text x="${x}" y="${y + 3}" font-size="8" fill="#ffc94d" text-anchor="middle" stroke="#222" stroke-width=".5">?</text></g>`;
  return "";
}
const EXTRAS = {
  Planner: (tx, ty, w) => box(tx + w - 0.75, ty + 0.25, 17, 0.45, 0.32, 1.5, "#f4f1de") + box(tx + w - 0.7, ty + 0.3, 18.5, 0.45, 0.32, 1.5, "#ffe39a"),
  Setup: (tx, ty, w) => box(tx + w - 0.7, ty + 0.25, 17, 0.4, 0.3, 4, "#c0392b") + box(tx + w - 0.7, ty + 0.25, 21, 0.4, 0.3, 4, "#2980b9") + box(tx + w - 0.7, ty + 0.25, 25, 0.4, 0.3, 4, "#27ae60"),
  Implementer: (tx, ty, w) => box(tx + w - 0.62, ty + 0.35, 17, 0.25, 0.25, 7, "#f4f1de"),
  Reviewer: (tx, ty, w) => box(tx + w - 0.75, ty + 0.25, 17, 0.45, 0.32, 1.5, "#f4f1de") + box(tx + w - 0.5, ty + 0.3, 18.5, 0.12, 0.12, 6, "#c0392b"),
  Device: (tx, ty, w, on) => box(tx + w - 0.6, ty + 0.3, 17, 0.22, 0.4, 2, "#111") + poly([iso(tx + w - 0.57, ty + 0.34, 19.2), iso(tx + w - 0.41, ty + 0.34, 19.2), iso(tx + w - 0.41, ty + 0.66, 19.2), iso(tx + w - 0.57, ty + 0.66, 19.2)], on ? "#7fe0ff" : "#2c3b47"),
  Delivery: (tx, ty, w) => box(tx + w - 0.8, ty + 0.2, 17, 0.55, 0.45, 9, "#c8a46a") + box(tx + w - 0.8, ty + 0.2, 26, 0.55, 0.45, 1, "#a8844a"),
  Orchestrator: (tx, ty, w) => box(tx + w - 0.6, ty + 0.3, 17, 0.25, 0.25, 7, "#f4f1de") + box(tx + 0.95, ty + 0.35, 17, 0.5, 0.3, 1.2, "#2c3e50"),
  "Tech Lead": (tx, ty, w) => box(tx + 0.15, ty + 0.3, 17, 0.3, 0.3, 0.8, "#ffe39a") + box(tx + 0.2, ty + 0.5, 17.8, 0.3, 0.3, 0.8, "#9ad0ff") + box(tx + w - 0.6, ty + 0.3, 17, 0.25, 0.25, 7, "#f4f1de"),
  "Implementer 2": (tx, ty, w) => box(tx + w - 0.55, ty + 0.35, 17, 0.25, 0.25, 7, "#ffd6a5"),
  "Implementer 3": (tx, ty, w) => box(tx + w - 0.55, ty + 0.35, 17, 0.25, 0.25, 7, "#caffbf"),
};
// A soft contact shadow on the floor, offset away from the light (top-left).
const floorShadow = (f, spread = 0.22) => poly([iso(f.x + 0.06, f.y + 0.06), iso(f.x + f.w + spread, f.y + 0.06), iso(f.x + f.w + spread, f.y + f.d + spread), iso(f.x + 0.06, f.y + f.d + spread)], "rgba(25,20,35,.16)", 'stroke="none"');
// One drawing per furniture kind, from the SIM plan (the single source of positions).
function drawFurniture(f, ctx) {
  const shadowed = ["desk", "machine", "counter", "fridge", "table", "sofa-seat", "plant", "cooler", "tv"].includes(f.kind);
  return (shadowed ? floorShadow(f) : "") + drawFurnitureBody(f, ctx);
}
function drawFurnitureBody(f, ctx) {
  const desk = f.role ? deskOf(f.role) : null, working = desk && desk.state === "working";
  const dim = desk && desk.state === "skipped" ? 'opacity=".42"' : "";
  if (f.kind === "desk") {
    const { x: tx, y: ty, w: wide } = f, screen = ctx.screenFor(f.role);
    let d = box(tx, ty, 0, wide, 0.9, 17, f.role === "Orchestrator" ? "#6d3f22" : "#9a6a3f", dim);
    // The monitor faces its owner, who faces the front of the room: we see its back, Habbo style.
    // A screen in use lights the monitor's edges and the desk around it.
    const mx = tx + wide / 2 - 0.27, my = ty + 0.06;
    if (screen.lines) {
      const [gx, gy] = iso(mx + 0.27, my + 0.42, 17);  // light spilling on the desktop, in front of the monitor
      d += `<ellipse cx="${gx.toFixed(1)}" cy="${gy.toFixed(1)}" rx="15" ry="6" fill="${screen.fill}" opacity=".2" ${screen.cls}/>`;
    }
    d += box(mx + 0.2, my + 0.04, 17, 0.14, 0.08, 4, "#2b2e38", dim);
    d += box(mx, my, 21, 0.54, 0.12, 12, "#2b2e38", dim);
    d += poly([iso(mx + 0.2, my + 0.12, 28.5), iso(mx + 0.34, my + 0.12, 28.5), iso(mx + 0.34, my + 0.12, 26.5), iso(mx + 0.2, my + 0.12, 26.5)], "#6b7080");
    if (screen.lines) d += poly([iso(mx, my, 33), iso(mx + 0.54, my, 33), iso(mx + 0.54, my + 0.12, 33), iso(mx, my + 0.12, 33)], screen.fill, screen.cls);
    if (EXTRAS[f.role] && !dim) d += EXTRAS[f.role](tx, ty, wide, working);
    return d;
  }
  if (f.kind === "chair") return box(f.x, f.y + 0.07, 0, 0.5, 0.45, 10, "#454a5c", dim) + box(f.x, f.y, 10, 0.5, 0.08, 18, "#3a3e4e", dim);
  if (f.kind === "machine") {
    const lamp = desk.state === "done" ? "#4fd07d" : desk.state === "failed" ? "#ff6262" : working ? "#ffc94d" : "#555";
    const { x, y } = f, fy = y + 0.9;
    let m = box(x, y, 0, 1.1, 0.9, 54, "#9aa4b2", dim);
    m += poly([iso(x + 0.12, fy, 46), iso(x + 0.98, fy, 46), iso(x + 0.98, fy, 30), iso(x + 0.12, fy, 30)], working ? "#1d4a5e" : "#13212b", working ? 'class="screen-on"' : "");
    m += poly([iso(x + 0.2, fy, 24), iso(x + 0.36, fy, 24), iso(x + 0.36, fy, 18), iso(x + 0.2, fy, 18)], lamp, working ? 'class="lamp-on"' : "");
    for (let i = 0; i < 3; i++) m += poly([iso(x + 0.5, fy, 24 - i * 4), iso(x + 0.95, fy, 24 - i * 4), iso(x + 0.95, fy, 22.6 - i * 4), iso(x + 0.5, fy, 22.6 - i * 4)], "#5d6673");
    return m;
  }
  if (f.kind === "plant") {
    const [px, py] = iso(f.x + 0.22, f.y + 0.22, 14);
    return box(f.x, f.y, 0, 0.45, 0.45, 14, "#b5643a") + `<ellipse cx="${px}" cy="${py - 10}" rx="13" ry="12" fill="#3f9b4b" stroke="rgba(0,0,0,.3)"/><ellipse cx="${px - 5}" cy="${py - 16}" rx="7" ry="7" fill="#57b864"/>`;
  }
  if (f.kind === "cooler") {
    const [px, py] = iso(f.x + 0.25, f.y + 0.25, 26);
    return box(f.x, f.y, 0, 0.5, 0.5, 26, "#e8e8ee") + `<ellipse cx="${px}" cy="${py - 9}" rx="8" ry="10" fill="#8fd3ff" stroke="rgba(0,0,0,.3)"/>`;
  }
  if (f.kind === "counter") {
    let c = box(f.x, f.y, 0, f.w, f.d, 22, "#7b5a43") + box(f.x - 0.02, f.y - 0.02, 22, f.w + 0.04, f.d + 0.04, 2, "#e9e4da");
    c += box(f.x + 0.3, f.y + 0.12, 24, 0.55, 0.42, 20, "#2d2f36") + poly([iso(f.x + 0.38, f.y + 0.54, 38), iso(f.x + 0.78, f.y + 0.54, 38), iso(f.x + 0.78, f.y + 0.54, 31), iso(f.x + 0.38, f.y + 0.54, 31)], "#ff9f43");
    c += box(f.x + 1.15, f.y + 0.2, 24, 0.16, 0.16, 5, "#fff") + box(f.x + 1.4, f.y + 0.2, 24, 0.16, 0.16, 5, "#fff") + box(f.x + 1.9, f.y + 0.12, 24, 0.5, 0.4, 7, "#c0392b");
    return c;
  }
  if (f.kind === "fridge") return box(f.x, f.y, 0, f.w, f.d, 54, "#dfe6ee") + poly([iso(f.x + 0.1, f.y + f.d, 46), iso(f.x + 0.14, f.y + f.d, 46), iso(f.x + 0.14, f.y + f.d, 32), iso(f.x + 0.1, f.y + f.d, 32)], "#8a96a3");
  if (f.kind === "table") return box(f.x + 0.5, f.y + 0.4, 0, 0.2, 0.2, 14, "#5c4033") + box(f.x, f.y, 14, f.w, f.d, 3, "#c98d5a") + box(f.x + 0.3, f.y + 0.35, 17, 0.14, 0.14, 4, "#fff");
  if (f.kind === "stool") return box(f.x + 0.16, f.y + 0.16, 0, 0.12, 0.12, 8, "#555") + box(f.x, f.y, 8, f.w, f.d, 3, "#e67e22");
  if (f.kind === "sofa-seat") return box(f.x, f.y, 0, f.w, f.d, 10, "#3f6fb5") + box(f.x + 0.05, f.y + 0.05, 10, f.w / 2 - 0.08, f.d - 0.1, 2.5, "#4a7cc4") + box(f.x + f.w / 2 + 0.03, f.y + 0.05, 10, f.w / 2 - 0.08, f.d - 0.1, 2.5, "#4a7cc4");
  if (f.kind === "sofa-back") return box(f.x, f.y, 0, f.w, f.d, 24, "#355f9c");
  if (f.kind === "sofa-arm") return box(f.x, f.y, 0, f.w, f.d, 15, "#2f5590");
  if (f.kind === "tv") {  // low stand, console and a TV whose screen faces the room (+y)
    const playing = Object.values(PLACED).some(pl => pl && pl.mode === "sofa");
    let tv = box(f.x, f.y, 0, f.w, f.d, 12, "#5d4037") + box(f.x + 0.15, f.y + 0.1, 12, 0.45, 0.3, 4, "#1c1c22");
    tv += poly([iso(f.x + 0.22, f.y + 0.4, 14.5), iso(f.x + 0.32, f.y + 0.4, 14.5), iso(f.x + 0.32, f.y + 0.4, 13.5), iso(f.x + 0.22, f.y + 0.4, 13.5)], playing ? "#2ecc71" : "#555");
    tv += box(f.x + 0.95, f.y + 0.28, 12, 0.22, 0.12, 2, "#2d3436") + box(f.x + 1.35, f.y + 0.2, 12, 0.14, 0.1, 4, "#2d3436");
    tv += box(f.x + 0.75, f.y + 0.15, 12, 0.5, 0.15, 6, "#111");
    tv += box(f.x + 0.05, f.y + 0.12, 18, 1.9, 0.12, 28, "#15151b");
    const sy = f.y + 0.24, screen = [iso(f.x + 0.14, sy, 44), iso(f.x + 1.86, sy, 44), iso(f.x + 1.86, sy, 20), iso(f.x + 0.14, sy, 20)];
    tv += poly(screen, playing ? "#2c3e8f" : "#20232b", playing ? 'class="screen-on"' : "");
    if (playing) {  // a little platform game
      tv += poly([iso(f.x + 0.14, sy, 25), iso(f.x + 1.86, sy, 25), iso(f.x + 1.86, sy, 20), iso(f.x + 0.14, sy, 20)], "#27ae60");
      tv += poly([iso(f.x + 0.55, sy, 31), iso(f.x + 0.75, sy, 31), iso(f.x + 0.75, sy, 25), iso(f.x + 0.55, sy, 25)], "#e74c3c", 'class="zz"');
      tv += poly([iso(f.x + 1.2, sy, 36), iso(f.x + 1.5, sy, 36), iso(f.x + 1.5, sy, 33), iso(f.x + 1.2, sy, 33)], "#f1c40f");
      tv += poly([iso(f.x + 1.6, sy, 41), iso(f.x + 1.72, sy, 41), iso(f.x + 1.72, sy, 39), iso(f.x + 1.6, sy, 39)], "#fff");
    }
    return tv;
  }
  return "";
}
// Painter's order for an isometric scene: a dependency sort on footprints, so a walking agent can be
// in front of one desk and behind the next. Items whose screen boxes do not overlap need no order.
function screenBox(it) {
  const h = it.h || 40;
  return { x0: iso(it.x, it.y + it.d)[0], x1: iso(it.x + it.w, it.y)[0], y0: iso(it.x, it.y, h)[1], y1: iso(it.x + it.w, it.y + it.d)[1] };
}
function behind(a, b) {
  const e = 1e-3;
  const ax = a.x + a.w <= b.x + e, ay = a.y + a.d <= b.y + e, bx = b.x + b.w <= a.x + e, by = b.y + b.d <= a.y + e;
  if ((ax || ay) && !(bx || by)) return true;
  if ((bx || by) && !(ax || ay)) return false;
  if (!(ax || ay || bx || by)) return (a.prio || 0) < (b.prio || 0) || ((a.prio || 0) === (b.prio || 0) && a.x + a.y < b.x + b.y);
  return a.x + a.w / 2 + a.y + a.d / 2 < b.x + b.w / 2 + b.y + b.d / 2;
}
function depthSort(items) {
  const n = items.length, boxes = items.map(screenBox), after = items.map(() => []), indeg = new Array(n).fill(0);
  for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++) {
    const a = boxes[i], b = boxes[j];
    if (a.x1 <= b.x0 || b.x1 <= a.x0 || a.y1 <= b.y0 || b.y1 <= a.y0) continue;
    const [first, second] = behind(items[i], items[j]) ? [i, j] : [j, i];
    after[first].push(second); indeg[second]++;
  }
  const key = i => items[i].x + items[i].w + items[i].y + items[i].d, done = new Array(n).fill(false), out = [];
  while (out.length < n) {
    let pick = -1;
    for (let i = 0; i < n; i++) if (!done[i] && indeg[i] === 0 && (pick < 0 || key(i) < key(pick))) pick = i;
    if (pick < 0) for (let i = 0; i < n; i++) if (!done[i] && (pick < 0 || key(i) < key(pick))) pick = i;  // cycle: break it
    done[pick] = true; out.push(items[pick]);
    for (const j of after[pick]) indeg[j]--;
  }
  return out;
}
// Where every agent is at time `now` (seconds): the deterministic routine in SIM decides.
function placements(now) {
  const out = {}, seed = DATA.ticket || "office", work = DATA.work || {};
  for (const role of OFFICE.AGENTS) out[role] = { role, ...OFFICE.stateAt(seed, role, now, work) };
  return out;
}
/* ---------- characters: cute Habbo-style people seen three-quarters ---------- */
// Base drawing faces south-west (+y, toward the front of the room); south-east (+x) mirrors it.
// North-east (-y) and north-west (-x) are the back views. Rounded limbs with an outline, big head.
const STYLE = {  // per agent: accessory and hair, so each one is recognisable anywhere in the room
  Orchestrator: { acc: "tie", hair: "short" }, Setup: { acc: "beanie", hair: "short" }, Planner: { acc: "glasses", hair: "side" },
  Implementer: { acc: "headphones", hair: "messy" }, Reviewer: { acc: "glasses", hair: "long" }, Device: { acc: "bun", hair: "bun" },
  Delivery: { acc: "cap", hair: "short" }, "Tech Lead": { acc: "tie", hair: "side" },
  "Implementer 2": { acc: "headphones", hair: "long" }, "Implementer 3": { acc: "beanie", hair: "messy", tint: "#8e44ad" },
};
const OL = "#2a2733", OW = 1, PANTS = "#3f5a78", SHOE = "#2b2b33";
function palette(desk) {
  const [hair, skin] = LOOK[desk.role] || ["#3b2a20", "#f2c39b"];
  return { hair, hairD: shade(hair, .75), skin, skinD: shade(skin, .86), shirt: desk.colour, shirtD: shade(desk.colour, .76),
    pants: PANTS, pantsD: shade(PANTS, .78) };
}
const n1 = v => (+v).toFixed(1);
const oval = (cx, cy, rx, ry, fill, extra = "") => `<ellipse cx="${n1(cx)}" cy="${n1(cy)}" rx="${rx}" ry="${ry}" fill="${fill}" stroke="${OL}" stroke-width="${OW}" ${extra}/>`;
const shape = (d, fill, extra = "") => `<path d="${d}" fill="${fill}" stroke="${OL}" stroke-width="${OW}" stroke-linejoin="round" ${extra}/>`;
// A rounded limb: an outline stroke under a coloured stroke, both with round caps.
function limb(points, width, colour) {
  const d = "M" + points.map(([x, y]) => `${n1(x)} ${n1(y)}`).join(" L");
  return `<path d="${d}" fill="none" stroke="${OL}" stroke-width="${width + 2 * OW}" stroke-linecap="round" stroke-linejoin="round"/><path d="${d}" fill="none" stroke="${colour}" stroke-width="${width}" stroke-linecap="round" stroke-linejoin="round"/>`;
}
function chibiHead(desk, pal, back, cx, cy) {
  const st = STYLE[desk.role] || {};
  let h = "";
  if (st.hair === "bun") h += oval(cx + (back ? -3 : 4), cy - 9.6, 3.6, 3.4, pal.hair);
  if (st.hair === "long") h += shape(`M${cx - (back ? 9 : -3)} ${cy - 2} L${cx - (back ? 9.6 : -2)} ${cy + 13} Q${cx} ${cy + 15} ${cx + (back ? 9.6 : 9.8)} ${cy + 12} L${cx + 9.6} ${cy - 2} Z`, pal.hairD);
  h += oval(cx, cy, 9.6, 9, pal.skin);
  if (back) {  // seen from behind: the back of the head is hair, an ear peeks out on the left
    h += oval(cx - 8.6, cy + 1.6, 1.8, 2.4, pal.skinD);
    h += shape(`M${cx - 9.7} ${cy + 1.5} C${cx - 10.4} ${cy - 12.5} ${cx + 10.4} ${cy - 12.5} ${cx + 9.7} ${cy + 1.5} C${cx + 9} ${cy + 7.8} ${cx - 7.6} ${cy + 8.4} ${cx - 9.7} ${cy + 1.5} Z`, pal.hair);
    h += `<path d="M${cx - 4} ${cy - 6} q4 -3 8 -1" stroke="${shade(pal.hair, 1.3)}" stroke-width="1.2" fill="none" stroke-linecap="round" opacity=".7"/>`;
  } else {
    h += `<path d="M${cx + 4} ${cy - 7.6} C${cx + 10.6} ${cy - 4} ${cx + 10.6} ${cy + 4.6} ${cx + 3.6} ${cy + 8.6} C${cx + 7} ${cy + 3} ${cx + 7} ${cy - 3} ${cx + 4} ${cy - 7.6} Z" fill="${pal.skinD}" opacity=".7"/>`;
    h += oval(cx + 8.2, cy + 1.2, 1.8, 2.4, pal.skinD);
    const hair = {
      side: `M${cx - 9.8} ${cy - 0.5} C${cx - 10.4} ${cy - 13} ${cx + 10.6} ${cy - 13.5} ${cx + 9.8} ${cy + 1} L${cx + 9.3} ${cy + 4.5} C${cx + 7.8} ${cy + 2.4} ${cx + 7} ${cy - 0.6} ${cx + 6.3} ${cy - 3} C${cx + 3} ${cy - 5.4} ${cx - 1} ${cy - 3.8} ${cx - 4.2} ${cy - 5.8} C${cx - 6} ${cy - 3.4} ${cx - 8} ${cy - 1.8} ${cx - 9.8} ${cy - 0.5} Z`,
      messy: `M${cx - 10} ${cy} L${cx - 9} ${cy - 9} L${cx - 5.8} ${cy - 7.4} L${cx - 3.6} ${cy - 12.6} L${cx - 0.2} ${cy - 9.2} L${cx + 3.2} ${cy - 12.4} L${cx + 5.4} ${cy - 8.6} L${cx + 9.4} ${cy - 8} L${cx + 10.2} ${cy + 1.5} L${cx + 9.4} ${cy + 4.5} C${cx + 7.8} ${cy + 2} ${cx + 7} ${cy - 1} ${cx + 6.2} ${cy - 3.2} C${cx + 2} ${cy - 6} ${cx - 4} ${cy - 4.6} ${cx - 10} ${cy} Z`,
    }[st.hair] || `M${cx - 9.8} ${cy - 1} C${cx - 10.4} ${cy - 13} ${cx + 10.6} ${cy - 13.2} ${cx + 9.8} ${cy + 0.6} L${cx + 9.3} ${cy + 4.4} C${cx + 7.8} ${cy + 2.2} ${cx + 7} ${cy - 0.8} ${cx + 6.2} ${cy - 3.2} C${cx + 2.4} ${cy - 6.6} ${cx - 4} ${cy - 5.4} ${cx - 9.8} ${cy - 1} Z`;
    h += shape(hair, pal.hair);
    h += `<path d="M${cx - 2} ${cy - 9} q4 -2.4 8 0" stroke="${shade(pal.hair, 1.35)}" stroke-width="1.2" fill="none" stroke-linecap="round" opacity=".75"/>`;
    for (const ex of [cx - 6, cx - 1.4]) {
      h += `<ellipse cx="${n1(ex)}" cy="${n1(cy + 1)}" rx="1.55" ry="2.15" fill="#1f1d26"/><circle cx="${n1(ex + 0.5)}" cy="${n1(cy + 0.2)}" r=".6" fill="#fff"/>`;
    }
    h += `<path d="M${n1(cx - 5.2)} ${n1(cy + 5.2)} Q${n1(cx - 3.7)} ${n1(cy + 6.6)} ${n1(cx - 2.2)} ${n1(cy + 5.2)}" stroke="${OL}" stroke-width=".9" fill="none" stroke-linecap="round"/>`;
    h += `<ellipse cx="${n1(cx - 8)}" cy="${n1(cy + 4)}" rx="1.5" ry=".9" fill="#ff8a8a" opacity=".55"/><ellipse cx="${n1(cx + 0.8)}" cy="${n1(cy + 4.2)}" rx="1.4" ry=".85" fill="#ff8a8a" opacity=".5"/>`;
    if (st.acc === "glasses") h += `<g fill="none" stroke="${OL}" stroke-width=".9"><circle cx="${n1(cx - 6)}" cy="${n1(cy + 1)}" r="2.6"/><circle cx="${n1(cx - 1.4)}" cy="${n1(cy + 1)}" r="2.6"/><path d="M${n1(cx - 3.4)} ${n1(cy + 0.8)} h-.1 M${n1(cx + 1.2)} ${n1(cy + 0.6)} L${n1(cx + 7)} ${n1(cy - 0.2)}"/></g>`;
  }
  if (st.acc === "beanie") h += shape(`M${cx - 10} ${cy - 2.4} C${cx - 9.8} ${cy - 15.5} ${cx + 9.8} ${cy - 15.5} ${cx + 10} ${cy - 2.4} Z`, st.tint || "#e67e22")
    + `<rect x="${n1(cx - 10.2)}" y="${n1(cy - 4)}" width="20.4" height="3.4" rx="1.6" fill="${shade(st.tint || "#e67e22", .87)}" stroke="${OL}" stroke-width="${OW}"/>` + oval(cx, cy - 14.4, 2.2, 2.2, "#f5f0e6");
  if (st.acc === "cap") h += shape(`M${cx - 9.9} ${cy - 2.6} C${cx - 9.8} ${cy - 14.6} ${cx + 9.8} ${cy - 14.6} ${cx + 9.9} ${cy - 2} Z`, "#1abc9c")
    + (back ? "" : `<ellipse cx="${n1(cx - 10.5)}" cy="${n1(cy - 2.6)}" rx="6.4" ry="2.2" fill="#16a085" stroke="${OL}" stroke-width="${OW}" transform="rotate(-14 ${n1(cx - 10.5)} ${n1(cy - 2.6)})"/>`);
  if (st.acc === "headphones") h += `<path d="M${n1(cx - 9.6)} ${n1(cy - 1)} C${n1(cx - 10.4)} ${n1(cy - 15.5)} ${n1(cx + 10.4)} ${n1(cy - 15.5)} ${n1(cx + 9.6)} ${n1(cy)}" fill="none" stroke="${OL}" stroke-width="2.6"/>` + oval(back ? cx - 8.8 : cx + 8.6, cy + 1, 2.6, 3.4, "#34495e");
  return h;
}
function chibiTorso(desk, pal, back, top, bottom) {
  const st = STYLE[desk.role] || {};
  const front = back ? pal.shirtD : pal.shirt, side = back ? pal.shirt : pal.shirtD;
  let t = shape(`M-6.6 ${top + 3.4} Q-6.6 ${top} -3.4 ${top} L3 ${top} L3 ${bottom} L-6.6 ${bottom} Z`, front);
  t += shape(`M3 ${top} L4.2 ${top} Q6.4 ${top} 6.4 ${top + 3} L6.4 ${bottom - 0.8} L3 ${bottom} Z`, side);
  t += `<rect x="-6.6" y="${bottom - 2}" width="13" height="2" fill="${pal.pantsD}" stroke="${OL}" stroke-width=".8"/>`;
  if (!back) t += `<path d="M-4.6 ${top + 0.3} L-2 ${top + 3} L0.6 ${top + 0.3}" fill="${shade(pal.shirt, 1.2)}" stroke="${OL}" stroke-width=".8"/>`;
  if (!back && st.acc === "tie") t += shape(`M-2 ${top + 2.8} L-3.2 ${top + 5} L-2 ${top + 11} L-0.8 ${top + 5} Z`, "#c0392b");
  return t;
}
// pose: stand | stepA | stepB | sit | desk | type. Feet (or the seat, when sitting) at (fx, fy).
// At a desk (desk, type) the legs are under the desktop, so they are not drawn.
function figure(desk, fx, fy, face, pose, s = 1.12) {
  const pal = palette(desk), back = face === "-x" || face === "-y", flip = face === "+x" || face === "-x" ? -1 : 1;
  const atDesk = pose === "desk" || pose === "type", sitting = pose === "sit" || atDesk, walking = pose === "stepA" || pose === "stepB";
  const lift = sitting ? 11 : 0, a = walking ? (pose === "stepA" ? 1 : -1) : 0;
  const top = -26 + lift, bottom = -11 + lift, hy = -35 + lift;
  const shoe = (x, y, dir) => oval(x + dir * 1.2, y, 3.3, 1.9, SHOE);
  let g = sitting ? "" : `<ellipse cx="0" cy="0" rx="10.5" ry="3.9" fill="url(#g-shadow)"/>`;
  const legsStanding = () => limb([[2.4, bottom - 1], [2.6 + 2 * a, -2.6 - a]], 4.6, pal.pantsD) + shoe(2.6 + 2 * a, -1.8 - a, back ? 1 : -1)
    + limb([[-2.4, bottom - 1], [-2.6 - 2.2 * a, -1.6 + a]], 4.6, pal.pants) + shoe(-2.6 - 2.2 * a, -1 + a, back ? 1 : -1);
  const thighsFront = () => limb([[2.4, bottom - 1.5], [-3.6, bottom + 2], [-3.9, bottom + 8.6]], 4.6, pal.pantsD) + shoe(-4.2, bottom + 9.4, -1)
    + limb([[-1.8, bottom - 1], [-7.6, bottom + 2.6], [-7.9, bottom + 9.4]], 4.6, pal.pants) + shoe(-8.4, bottom + 10.2, -1);
  const thighsBack = () => limb([[-2, bottom - 1.5], [4.4, bottom - 4.2], [4.8, bottom + 2.6]], 4.6, pal.pantsD)
    + limb([[2.2, bottom - 1.5], [7.8, bottom - 3.4], [8.2, bottom + 3.4]], 4.6, pal.pants) + shoe(8.6, bottom + 4, 1);
  const armNear = () => {
    if (pose === "type") return limb([[-5.6, top + 2.6], [-9.6, top + 8.2]], 3.8, pal.shirt) + oval(-10.2, top + 9, 2.1, 2.1, pal.skin, 'class="arm l"');
    const hx = -7.6 - 2.4 * a * (back ? -1 : 1), hy2 = top + 11 - Math.abs(a);
    return limb([[-6, top + 2.6], [hx, hy2]], 3.8, back ? pal.shirtD : pal.shirt) + oval(hx, hy2 + 0.6, 2.1, 2.1, pal.skin);
  };
  const armFar = () => {
    if (pose === "type") return limb([[4.6, top + 2.6], [-1.6, top + 8.6]], 3.8, pal.shirtD) + oval(-2.2, top + 9.4, 2.1, 2.1, pal.skinD, 'class="arm r"');
    const hx = 6.2 + 2.2 * a * (back ? -1 : 1), hy2 = top + 10.6 - Math.abs(a);
    return limb([[5, top + 2.6], [hx, hy2]], 3.8, back ? pal.shirt : pal.shirtD) + oval(hx, hy2 + 0.6, 2.1, 2.1, pal.skinD);
  };
  if (back) {
    g += sitting ? (atDesk ? "" : thighsBack()) : legsStanding();
    g += armFar() + chibiTorso(desk, pal, true, top, bottom) + armNear();
  } else {
    if (!sitting) g += legsStanding();
    g += (pose === "type" ? "" : armFar()) + chibiTorso(desk, pal, false, top, bottom);
    if (sitting && !atDesk) g += thighsFront();
    g += (pose === "type" ? armFar() : "") + armNear();
  }
  g += chibiHead(desk, pal, back, -0.6, hy);
  const near = pose === "type" ? [-10.2, top + 9] : [-7.6, top + 11];
  const hand = [fx + flip * near[0] * s, fy + near[1] * s];
  return { svg: `<g transform="translate(${n1(fx)} ${n1(fy)}) scale(${(flip * s).toFixed(2)} ${s})">${g}</g>`, top: fy + (hy - 14) * s, hand };
}

// Small pictograms for what an idle agent is doing (drawn, so they look the same in every browser).
function icon(mode, x, y) {
  const g = body => `<g transform="translate(${x.toFixed(1)} ${y.toFixed(1)})">${body}</g>`;
  if (mode === "coffee" || mode === "copa") return g('<rect x="-4" y="-3" width="7" height="6" rx="1" fill="#fff" stroke="#5b3a29" stroke-width="1"/><path d="M3 -1.5 h1.6 v3 h-1.6" fill="none" stroke="#5b3a29" stroke-width="1"/><path d="M-2 -5 q1 -1.5 0 -3 M1 -5 q1 -1.5 0 -3" stroke="#ddd" stroke-width=".8" fill="none"/>');
  if (mode === "game" || mode === "sofa") return g('<rect x="-6" y="-3" width="12" height="6" rx="3" fill="#2c3e50" stroke="#111" stroke-width=".8"/><rect x="-4" y="-.5" width="3" height="1" fill="#fff"/><rect x="-3" y="-1.5" width="1" height="3" fill="#fff"/><circle cx="3" cy="-1" r=".9" fill="#e74c3c"/><circle cx="4.4" cy=".6" r=".9" fill="#f1c40f"/>');
  if (mode === "cooler") return g('<path d="M0 -5 C3 -1 3.5 1 0 3.5 C-3.5 1 -3 -1 0 -5 Z" fill="#5dade2" stroke="#1f618d" stroke-width=".8"/>');
  if (mode === "window") return g('<circle r="3.4" fill="#f9d342" stroke="#c9a227" stroke-width=".8"/><path d="M0 -6 v1.6 M0 4.4 v1.6 M-6 0 h1.6 M4.4 0 h1.6" stroke="#f9d342" stroke-width="1"/>');
  return "";
}
let PLACED = {};
function screenFor(role) {
  const pl = PLACED[role], desk = deskOf(role);
  if (desk.state === "working" && pl && pl.mode === "desk") return { fill: "#7fe0ff", cls: 'class="screen-on"', lines: "#0f3a4a" };
  if (pl && pl.mode === "game") return { fill: "#2ecc71", cls: 'class="screen-on"', lines: "#f1c40f" };
  return { fill: "#2c3b47", cls: "", lines: null };
}
function agentItem(desk, pl) {
  const base = { id: `agent:${desk.role}`, x: pl.x - 0.2, y: pl.y - 0.2, w: 0.4, d: 0.4, h: 50, prio: 3, desk, pl };
  if (pl.mode === "desk" || pl.mode === "game") {
    const [ax, ay] = iso(pl.x, pl.y, 10), typing = desk.state === "working" && pl.mode === "desk" && pl.working;
    const p = figure(desk, ax, ay, pl.face || "+y", typing || pl.mode === "game" ? "type" : "desk");
    return { ...base, svg: `<g class="${typing ? "typing" : ""}">${p.svg}</g>`, anchor: [ax, ay], top: p.top };
  }
  const seated = pl.mode === "copa" || pl.mode === "sofa";
  const [fx, fy] = iso(pl.x, pl.y, seated ? 10 : 0);
  const legs = pl.mode === "walk" ? (pl.step ? "stepA" : "stepB") : seated ? "sit" : "stand";
  const f = figure(desk, fx, fy, pl.face, legs);
  let props = "";
  const back = pl.face === "-x" || pl.face === "-y";
  if ((pl.mode === "coffee" || pl.mode === "copa") && !back) {
    const [hx, hy] = f.hand;
    props += `<rect x="${(hx - 2.5).toFixed(1)}" y="${(hy - 5).toFixed(1)}" width="5" height="5.2" rx="1" fill="#fff" stroke="${OL}" stroke-width=".8"/><path d="M${(hx - 1).toFixed(1)} ${(hy - 7).toFixed(1)} q1.5 -2 0 -4" stroke="#fff" stroke-width=".8" fill="none" class="zz"/>`;
  }
  if (pl.mode === "chat") props += `<g class="zz"><rect x="${(fx + 4).toFixed(1)}" y="${(f.top - 13).toFixed(1)}" width="17" height="10" rx="3" fill="#fff" stroke="#222" stroke-width=".8"/><text x="${(fx + 12.5).toFixed(1)}" y="${(f.top - 6).toFixed(1)}" font-size="6" text-anchor="middle" fill="#111">\u2026</text></g>`;
  return { ...base, svg: f.svg + props, anchor: [fx, fy], top: f.top - (pl.mode === "chat" ? 14 : 0) };
}
function overlayFor(desk, anchor, top, pl, lifted) {
  const [ax, ay] = anchor, working = desk.state === "working";
  const above = lifted != null ? lifted : Math.min(top, ay - 30) - 6, floating = ["idle", "waiting"].includes(desk.state);
  if (pl && pl.mode !== "desk") {  // away from the desk: a light name tag and what they are up to
    return tag(ax, above, desk) + icon(pl.mode, ax, above - 20);
  }
  let over = tag(ax, above, desk) + (floating ? statusMark(ax, above - 20, desk) : statusMark(ax + desk.role.length * 3 + 17, above - 6, desk));
  if (working && desk.note) over += bubble(ax, above - 16, desk.note);
  if (desk.state === "failed" && desk.note) over += bubble(ax, above - 16, desk.note, true);
  return over;
}
// Two walkers about to bump into each other each step aside, across the way they are going (drawing only).
function separateWalkers(placed) {
  const walkers = OFFICE.AGENTS.filter(r => placed[r] && placed[r].mode === "walk");
  for (let i = 0; i < walkers.length; i++) for (let j = i + 1; j < walkers.length; j++) {
    const a = placed[walkers[i]], b = placed[walkers[j]];
    if (Math.abs(a.x - b.x) + Math.abs(a.y - b.y) >= 0.4) continue;
    for (const [w, side] of [[a, 1], [b, -1]]) {
      if (w.face === "+x" || w.face === "-x") w.y += 0.18 * side; else w.x += 0.18 * side;
    }
  }
  return placed;
}
// The room is layered: walls, floor, hover rings and desk hit areas are built once; furniture and agents
// (depth-sorted), overlays and the agents' own hit areas are redrawn only when someone moved.
let FURNITURE_ITEMS = null, FURNITURE_KEY = "", SCENE_KEY = "", CLOCKS_KEY = "", animTimer = null;
// Where each name tag was drawn last, so the clocks and file cards can sit next to it.
let TAGS = {};
function renderRoom() {
  const svg = document.getElementById("room");
  svg.setAttribute("viewBox", `0 0 ${VW} ${VH}`);
  const present = new Set(desks().map(d => d.role)), rings = [];
  for (const f of OFFICE.FURNITURE) {
    if (!(f.kind === "desk" || f.kind === "machine") || !present.has(f.role)) continue;
    const ring = [iso(f.x - 0.2, f.y - 1.05), iso(f.x + f.w + 0.2, f.y - 1.05), iso(f.x + f.w + 0.2, f.y + f.d + 0.3), iso(f.x - 0.2, f.y + f.d + 0.3)];
    rings.push(`<polygon class="ring" data-ring="${esc(f.role)}" points="${pts(ring)}"/>`);
  }
  const deskHits = deskHitAreas();
  svg.innerHTML = `<defs id="roomDefs"><radialGradient id="g-shadow"><stop offset="0" stop-color="#000" stop-opacity=".32"/><stop offset="1" stop-color="#000" stop-opacity="0"/></radialGradient><linearGradient id="g-wall" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fff" stop-opacity=".16"/><stop offset="1" stop-color="#000" stop-opacity=".12"/></linearGradient></defs><g id="bg">${walls()}${floorTiles()}${rings.join("")}</g><g id="scene"></g><g id="overlay"></g><g id="clocks"></g><g id="deskHits">${deskHits.join("")}</g><g id="agentHits"></g>`;
  bindRoomEvents(svg);
  FURNITURE_ITEMS = null; SCENE_KEY = ""; CLOCKS_KEY = "";
  drawScene();
  clearTimeout(animTimer);
  const tick = () => { if (!document.hidden) drawScene(); animTimer = setTimeout(() => requestAnimationFrame(tick), 80); };
  animTimer = setTimeout(() => requestAnimationFrame(tick), 80);
}
function drawScene() {
  const svg = document.getElementById("room");
  PLACED = separateWalkers(placements(Date.now() / 1000));
  const key = OFFICE.AGENTS.map(r => { const p = PLACED[r]; return p ? `${p.mode}:${p.x.toFixed(2)}:${p.y.toFixed(2)}:${p.face}:${p.step || 0}:${p.working ? 1 : 0}` : ""; }).join("|");
  if (key === SCENE_KEY) return;  // nobody moved: keep the DOM as it is
  SCENE_KEY = key;
  const furnitureKey = OFFICE.AGENTS.map(r => PLACED[r] ? `${PLACED[r].mode}${PLACED[r].working ? 1 : 0}` : "").join();
  if (!FURNITURE_ITEMS || furnitureKey !== FURNITURE_KEY) {
    const present = new Set(desks().map(d => d.role));
    FURNITURE_ITEMS = OFFICE.FURNITURE.filter(f => !f.role || present.has(f.role))
      .map(f => ({ ...f, prio: f.kind === "chair" || f.kind === "stool" ? 0 : 2, svg: drawFurniture(f, { screenFor }) }));
    FURNITURE_KEY = furnitureKey;
  }
  const items = [...FURNITURE_ITEMS], overlays = [], agentHits = [], labels = [], placedTags = [];
  TAGS = {};
  for (const desk of desks()) {
    if (desk.kind === "machine") {
      const f = OFFICE.FURNITURE.find(x => x.id === "gate"), [hx, hy] = iso(f.x + 0.55, f.y + 0.45, 54);
      let over = tag(hx, hy - 8, desk) + statusMark(hx + desk.role.length * 3 + 17, hy - 14, desk);
      TAGS[desk.role] = { x: hx, y: hy - 8, w: desk.role.length * 6 + 18, at: null };
      if (desk.note && (desk.state === "failed" || desk.state === "working")) over += bubble(hx, hy - 24, desk.note, desk.state === "failed");
      overlays.push(over);
      continue;
    }
    const pl = PLACED[desk.role];
    if (!pl) continue;
    const it = agentItem(desk, pl);
    items.push(it);
    labels.push({ desk, it, pl });
    if (pl.mode !== "desk" && pl.mode !== "game") agentHits.push(agentHit(desk, it.anchor[0], it.anchor[1]));
  }
  // Labels must not cover each other: a name tag with what sits on its line (clock, file card) and the
  // speech bubble above it. The ones further back move up until they are clear.
  labels.sort((a, b) => b.it.anchor[1] - a.it.anchor[1]);
  const gate = TAGS["Quality gate"];
  if (gate) placedTags.push({ x0: gate.x - gate.w / 2, x1: gate.x + gate.w / 2 + 40, y0: gate.y - 13, y1: gate.y + 1 });
  for (const { desk, it, pl } of labels) {
    const width = desk.role.length * 6 + 18, x = it.anchor[0];
    const natural = Math.min(it.top, it.anchor[1] - 30) - 6;
    let above = natural;
    const extra = lineWidth(desk, pl), bubbleH = pl.mode === "desk" && desk.note && ["working", "failed"].includes(desk.state) ? 30 : 0;
    const box = y => ({ x0: x - width / 2, x1: x + width / 2 + extra, y0: y - 13 - bubbleH, y1: y + 1 });
    const hits = () => { const b = box(above); return placedTags.some(o => b.x0 < o.x1 + 2 && o.x0 < b.x1 + 2 && b.y0 < o.y1 && o.y0 < b.y1); };
    for (let i = 0; i < 8 && hits(); i++) above -= 14;
    placedTags.push(box(above));
    // A tag lifted clear of the others keeps a dotted line down to its agent.
    if (natural - above > 10) overlays.push(`<path d="M${n1(x)} ${n1(above + 1)} V${n1(natural + 6)}" stroke="rgba(10,12,20,.75)" stroke-width="1.2" stroke-dasharray="2 2"/>`);
    TAGS[desk.role] = { x, y: above, w: width, at: pl.mode };
    overlays.push(overlayFor(desk, it.anchor, it.top, pl, above));
  }
  svg.querySelector("#scene").innerHTML = depthSort(items).map(i => i.svg).join("");
  svg.querySelector("#overlay").innerHTML = overlays.join("");
  svg.querySelector("#agentHits").innerHTML = agentHits.join("");
  tickClocks();
}
// How far right of the name tag its line goes while the agent works (clock, then the file card).
function lineWidth(desk, pl) {
  if (desk.state !== "working" || !live()) return 0;
  let w = 46;
  const files = EDITORS.includes(desk.role) && pl && pl.mode === "desk" ? changesFor(desk.role) : [];
  if (files.length) {
    const latest = files[0], name = latest.path.split("/").pop();
    w += (Math.min(name.length, 24) + 16 + (files.length > 1 ? 10 : 0)) * 4.7 + 21;
  }
  return w;
}
// Once a second: the working agents' clocks next to their name tags, and the file each editor is
// changing, on its desk. Redrawn on their own layer, so the scene itself is left alone.
function tickClocks() {
  const svg = document.getElementById("room");
  if (!svg || !svg.querySelector("#clocks")) return;
  const t = now();
  let out = "";
  for (const desk of desks()) {
    const pos = TAGS[desk.role];
    if (!pos || desk.state !== "working" || !live()) continue;
    // Right of the name tag (free while an agent works): its clock, then the file it is editing.
    let x = pos.x + pos.w / 2 + 3;
    const secs = liveSeconds(desk);
    if (secs != null) {
      const text = fmtS(secs), width = text.length * 5.6 + 10;
      out += `<g><rect x="${n1(x)}" y="${n1(pos.y - 12)}" width="${n1(width)}" height="13" rx="6.5" fill="#ffc94d" stroke="#1a1300" stroke-width=".6"/><text x="${n1(x + width / 2)}" y="${n1(pos.y - 3)}" font-size="5.8" text-anchor="middle" fill="#1a1300">${esc(text)}</text></g>`;
      x += width + 3;
    }
    if (!EDITORS.includes(desk.role) || pos.at !== "desk") continue;
    const files = changesFor(desk.role);
    if (!files.length) continue;
    const latest = files[0], fresh = latest.mtime && t - latest.mtime < 6;
    const name = latest.path.split("/").pop(), label = name.length > 24 ? name.slice(0, 23) + "…" : name;
    const delta = latest.status === "??" || latest.status === "A" ? "new" : latest.status === "D" ? "deleted" : `+${latest.added ?? "?"} −${latest.removed ?? "?"}`;
    const more = files.length > 1 ? ` · ${files.length} files` : "";
    const text = `${label} ${delta}${more}`, width = text.length * 4.7 + 18, top = pos.y - 12;
    out += `<g class="${fresh ? "pulse" : ""}"><rect x="${n1(x)}" y="${n1(top)}" width="${n1(width)}" height="13" rx="4" fill="${fresh ? "#fff7d6" : "#f4f1de"}" stroke="#2a2733" stroke-width=".7"/>`
      + `<path d="M${n1(x + 4)} ${n1(top + 2.5)} h4.6 l2 2 v6 h-6.6 Z" fill="#fff" stroke="#2a2733" stroke-width=".6"/>`
      + `<text x="${n1(x + 14)}" y="${n1(top + 8.8)}" font-size="4.9" fill="#1a1a22">${esc(text)}</text></g>`;
  }
  if (out !== CLOCKS_KEY) { svg.querySelector("#clocks").innerHTML = out; CLOCKS_KEY = out; }
  document.querySelectorAll("[data-clock]").forEach(el => { const v = fmtS(liveSeconds(deskOf(el.dataset.clock))); if (el.textContent !== v) el.textContent = v; });
  document.querySelectorAll("[data-ago]").forEach(el => { const v = ago(t - Number(el.dataset.ago)); if (el.textContent !== v) el.textContent = v; });
  document.querySelectorAll(".filebtn").forEach(b => b.classList.toggle("fresh", live() && t - lastTouch(b.dataset.role) < 10));
}
function deskHitAreas() {
  const hits = [];
  for (const desk of desks()) {
    if (desk.kind === "machine") {
      const f = OFFICE.FURNITURE.find(x => x.id === "gate"), [hx, hy] = iso(f.x + 0.55, f.y + 0.45, 54);
      hits.push(hitArea(desk, hx, hy + 30));
    } else if (OFFICE.SEATS[desk.role]) {
      const seat = OFFICE.SEATS[desk.role], [sx, sy] = iso(seat.x, seat.y, 12);
      hits.push(hitArea(desk, sx, sy));
    }
  }
  return hits;
}
// Only the labels change with a desk's state; keep the elements (and keyboard focus) when nothing did.
function refreshDeskHits() { setHTML(document.querySelector("#deskHits"), deskHitAreas().join("")); }
function hitArea(desk, hx, hy) {
  return `<g class="spot" data-role="${esc(desk.role)}" tabindex="0" role="button" aria-label="${esc(desk.role)}: ${esc(STATE_LABEL[desk.state] || desk.state)}. Open details."><rect class="hit" x="${(hx - 64).toFixed(1)}" y="${(hy - 96).toFixed(1)}" width="128" height="132" rx="10"/></g>`;
}
function agentHit(desk, x, y) {
  return `<g class="spot" data-role="${esc(desk.role)}" aria-label="${esc(desk.role)}"><rect class="hit" x="${(x - 16).toFixed(1)}" y="${(y - 52).toFixed(1)}" width="32" height="56" rx="6"/></g>`;
}
// One set of listeners on the room (delegation), so redrawing the scene never drops them or the focus.
function bindRoomEvents(svg) {
  if (svg.dataset.bound) return;
  svg.dataset.bound = "1";
  const spotOf = e => e.target && e.target.closest ? e.target.closest(".spot") : null;
  const ringOf = role => svg.querySelector(`.ring[data-ring="${CSS.escape(role)}"]`);
  const clear = () => svg.querySelectorAll(".ring.hover").forEach(r => r.classList.remove("hover"));
  svg.addEventListener("click", e => { const s = spotOf(e); if (s) openDrawer(s.dataset.role); });
  svg.addEventListener("keydown", e => { const s = spotOf(e); if (s && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); openDrawer(s.dataset.role); } });
  svg.addEventListener("mouseover", e => { const s = spotOf(e); clear(); const r = s && ringOf(s.dataset.role); if (r) r.classList.add("hover"); });
  svg.addEventListener("mouseleave", clear);
  svg.addEventListener("focusin", e => { const s = spotOf(e); const r = s && ringOf(s.dataset.role); if (r) r.classList.add("hover"); });
  svg.addEventListener("focusout", clear);
}

/* ---------- side panel ---------- */
function avatar(role, state) {
  return `<span class="avatar" style="background:${deskOf(role).colour}">${esc(role[0])}${state ? `<span class="dot ${esc(state)}"></span>` : ""}</span>`;
}
function renderSide() {
  const t = DATA.totals || {};
  let html;
  if (!DATA.ticket) {
    html = `<div class="empty">No run yet. Start <code>/android-workflow</code> in this app and the office comes alive.</div>`;
  } else {
    html = `<div class="stats"><div class="stat"><span>Status</span><b>${chip(runStatus())}</b></div><div class="stat"><span>Time</span><b data-clock="Orchestrator">${fmtS(liveSeconds(deskOf("Orchestrator")))}</b></div><div class="stat"><span>Tokens</span><b>${fmtT(t.tokens)}</b></div></div>`;
    if (DATA.pr) html += `<div class="alert ${(DATA.draft || []).length ? "warn" : "ok"}"><b>${(DATA.draft || []).length ? "Draft PR" : "Pull request"}:</b> <a href="${esc(DATA.pr)}" target="_blank" rel="noopener">${esc(DATA.pr)}</a></div>`;
    if (DATA.status === "paused") html += `<div class="alert warn"><b>Waiting for you in the chat.</b><br>${esc(DATA.question || "")}</div>`;
    if ((DATA.draft || []).length) html += `<div class="alert bad"><b>Still open:</b><ul>${DATA.draft.map(i => `<li>${esc(i)}</li>`).join("")}</ul></div>`;
  }
  setHTML(document.getElementById("summary"), html);
  setHTML(document.getElementById("files"), DATA.ticket ? roles().map(role => {
    const desk = deskOf(role), files = (DATA.artifacts || {})[role] || [];
    const media = (DATA.media.before || []).length + (DATA.media.after || []).length;
    const bits = files.map(f => f.label);
    if (role === "Device" && media) bits.unshift(`${media} screenshot(s)`);
    if (role === "Quality gate" && (DATA.logs || []).length) bits.push(`${DATA.logs.length} log(s)`);
    const edits = role === "Tech Lead" ? 0 : changesFor(role).length;
    if (edits) bits.unshift(`${edits} changed file(s)`);
    return `<button class="filebtn" data-role="${esc(role)}">${avatar(role, desk.state)}<div><div class="who">${esc(role)}</div><div class="what">${esc(bits.join(" · ") || "nothing yet")}</div></div></button>`;
  }).join("") : "");
  const rows = (DATA.timeline || []).slice().reverse();
  const feed = document.getElementById("feed"), scroll = feed.scrollTop;
  setHTML(feed, rows.length ? rows.map(r => {
    const role = ROLE_ALIAS[r.role] || r.role;
    return `<div class="msg" tabindex="0" data-role="${esc(role)}">${avatar(role)}<div class="body"><div class="head"><b>${esc(r.role)}</b>${chip(r.status)}<span class="time">${esc(r.time)}</span></div>${r.note ? `<div class="text">${esc(r.note)}</div>` : ""}</div></div>`;
  }).join("") : `<div class="empty">Nothing has happened yet.</div>`);
  feed.scrollTop = scroll;
  setHTML(document.getElementById("foot"), live() ? `<span class="livedot"></span>Live · updated ${esc(DATA.generated_at)}` : `Updated ${esc(DATA.generated_at)}`);
}
// Replace only what changed, so a refresh never flickers or drops the reader's place.
const LAST_HTML = new WeakMap();
function setHTML(el, html) { if (el && LAST_HTML.get(el) !== html) { el.innerHTML = html; LAST_HTML.set(el, html); } }
function bindSide() {  // delegated once: the side panel is redrawn in place
  const open = e => { const el = e.target.closest && e.target.closest(".filebtn, .msg"); if (el) openDrawer(el.dataset.role); };
  document.getElementById("files").addEventListener("click", open);
  document.getElementById("feed").addEventListener("click", open);
  document.getElementById("feed").addEventListener("keydown", e => { if (e.key === "Enter") open(e); });
}

/* ---------- renderers ---------- */
function inline(t) {
  return t.replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .replace(/(^|[\s(])\*([^*\s][^*]*)\*/g, "$1<i>$2</i>")
    .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
}
function markdown(src) {
  const lines = esc(String(src || "").replace(/^---\n[\s\S]*?\n---\n/, "")).split("\n");
  let out = "", i = 0, list = null;
  const close = () => { if (list) { out += `</${list}>`; list = null; } };
  const isRow = s => /^\s*\|.*\|\s*$/.test(s);
  while (i < lines.length) {
    const l = lines[i];
    let m;
    if (/^```/.test(l)) { close(); let code = ""; i++; while (i < lines.length && !/^```/.test(lines[i])) code += lines[i++] + "\n"; out += `<pre><code>${code}</code></pre>`; i++; continue; }
    if (isRow(l)) {
      close(); const rows = [];
      while (i < lines.length && isRow(lines[i])) rows.push(lines[i++]);
      const cells = r => r.trim().replace(/^\|/, "").replace(/\|$/, "").split(/(?<!\\)\|/).map(c => inline(c.trim().replace(/\\\|/g, "|")));
      const body = rows.filter(r => !/^\s*\|[\s|:-]+\|\s*$/.test(r));
      if (body.length) out += `<table><thead><tr>${cells(body[0]).map(c => `<th>${c}</th>`).join("")}</tr></thead><tbody>${body.slice(1).map(r => `<tr>${cells(r).map(c => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
      continue;
    }
    if ((m = l.match(/^(#{1,6})\s+(.*)/))) { close(); const n = Math.min(m[1].length + 1, 4); out += `<h${n}>${inline(m[2])}</h${n}>`; }
    else if ((m = l.match(/^\s*[-*+]\s+(.*)/))) { if (list !== "ul") { close(); out += "<ul>"; list = "ul"; } out += `<li>${inline(m[1].replace(/^\[ \]/, "☐").replace(/^\[x\]/i, "☑"))}</li>`; }
    else if ((m = l.match(/^\s*\d+[.)]\s+(.*)/))) { if (list !== "ol") { close(); out += "<ol>"; list = "ol"; } out += `<li>${inline(m[1])}</li>`; }
    else if ((m = l.match(/^&gt;\s?(.*)/))) { close(); out += `<blockquote>${inline(m[1])}</blockquote>`; }
    else if (/^\s*(---|\*\*\*)\s*$/.test(l)) { close(); out += "<hr>"; }
    else if (!l.trim()) { close(); }
    else { close(); out += `<p>${inline(l)}</p>`; }
    i++;
  }
  close();
  return `<div class="md">${out || '<p class="muted">Empty.</p>'}</div>`;
}
function highlight(value) {
  return esc(JSON.stringify(value, null, 2)).replace(/(&quot;(?:[^&\\]|\\.|&(?!quot;))*&quot;)(\s*:)?|\b(true|false|null)\b|-?\b\d+(?:\.\d+)?\b/g,
    (m, str, colon, lit) => str ? `<span class="${colon ? "j-key" : "j-str"}">${str}</span>${colon || ""}` : lit ? `<span class="j-lit">${m}</span>` : `<span class="j-num">${m}</span>`);
}
const SKIP_KEYS = ["id", "file", "path", "line", "summary", "message", "text", "detail", "description", "finding", "title"];
function finding(f) {
  if (typeof f !== "object" || f === null) return `<div class="item">${esc(f)}</div>`;
  const loc = [f.file || f.path, f.line].filter(v => v != null).join(":");
  const text = f.summary || f.title || f.message || f.text || f.detail || f.description || f.finding || "";
  const rest = Object.entries(f).filter(([k]) => !SKIP_KEYS.includes(k));
  return `<div class="item">${f.id ? `<b>${esc(f.id)}</b> ` : ""}${esc(text)}${loc ? `<div class="loc"><code>${esc(loc)}</code></div>` : ""}${rest.length ? `<div class="loc">${rest.map(([k, v]) => `${esc(k)}: ${esc(typeof v === "object" ? JSON.stringify(v) : v)}`).join(" · ")}</div>` : ""}</div>`;
}
const list = (title, items, render = x => `<div class="item"><code>${esc(x)}</code></div>`) =>
  `<div class="card"><h4>${title} (${(items || []).length})</h4>${(items || []).length ? items.map(render).join("") : '<div class="muted">None.</div>'}</div>`;
const VIEWS = {
  "gate-report.json": g => {
    let h = `<div class="kv"><span>Result</span><span>${chip(g.status || "not_run")}</span><span>Gate run</span><span>${esc(g.run ?? "—")}</span><span>Duration</span><span>${fmtS(g.duration_seconds)}</span>${g.reason ? `<span>Reason</span><span>${esc(g.reason)}</span>` : ""}</div>`;
    const steps = g.steps || [];
    h += steps.length ? `<table><thead><tr><th>Check</th><th>Result</th><th>Time</th></tr></thead><tbody>${steps.map(s => `<tr><td><code>${esc(String(s.command || "").replace(/^\.\/gradlew\s+/, ""))}</code>${(s.output_excerpt || []).length && !["passed", "waived"].includes(s.outcome) ? `<details><summary>What failed</summary><pre>${esc(s.output_excerpt.join("\n"))}</pre></details>` : ""}</td><td>${chip(s.outcome || "?")}</td><td>${fmtS(s.duration_seconds)}</td></tr>`).join("")}</tbody></table>` : `<p class="muted">The gate has not run yet. It runs after the Implementer.</p>`;
    if ((g.waivers || []).length) h += list("Waived (only in files this change does not touch)", g.waivers, w => `<div class="item"><code>${esc(w.command)}</code> — ${esc(w.count)} finding(s) in ${esc(w.file_count)} file(s)</div>`);
    if ((g.consumer_modules || []).length) h += list("Modules that use the change (compiled and tested too)", g.consumer_modules);
    if ((g.warnings || []).length) h += list("Warnings", g.warnings, finding);
    if (g.auto_format && (g.auto_format.formatted || []).length) h += list("Auto-formatted", g.auto_format.formatted);
    if (g.secret_scan && g.secret_scan.status) h += `<div class="kv"><span>Secret scan</span><span>${chip(g.secret_scan.status)}</span></div>`;
    return h;
  },
  "review.json": r => `<div class="kv"><span>Verdict</span><span>${chip(r.status || "not_run")}</span></div>` +
    list("Blocking", r.blocking, finding) + list("Concerns", r.concerns, finding) + list("Suggestions", r.suggestions, finding),
  "ticket-spec.json": s => {
    const t = s.ticket || {}, route = [...new Set((s.route || []).map(x => ROLE_OF_STAGE[x] || x))];
    let h = `<div class="kv"><span>Ticket</span><span><b>${esc(t.id)}</b> ${esc(t.title)}</span><span>Type</span><span>${chip(s.type || "?")}</span><span>Risk · complexity</span><span>${esc(s.risk)} · ${esc(s.complexity)}</span><span>Surfaces</span><span>${(s.surfaces || []).map(x => `<code>${esc(x)}</code>`).join(" ") || "—"}</span><span>Route</span><span>${route.map(esc).join(" → ")}</span>${s.pr_template ? `<span>PR template</span><span><code>${esc(s.pr_template)}</code></span>` : ""}</div>`;
    if (t.description) h += `<div class="card"><h4>Description</h4>${markdown(t.description)}</div>`;
    h += `<div class="card"><h4>Acceptance criteria (${(s.acceptance_criteria || []).length})</h4>${(s.acceptance_criteria || []).length ? `<ol>${s.acceptance_criteria.map(a => `<li>${esc(typeof a === "object" ? (a.text || a.criterion || JSON.stringify(a)) : a)}</li>`).join("")}</ol>` : '<div class="muted">None yet.</div>'}</div>`;
    if (s.reproduction) h += `<div class="card"><h4>Steps to reproduce</h4>${markdown(s.reproduction)}</div>`;
    if ((s.business_questions || []).length) h += list("Business questions", s.business_questions, q => `<div class="item"><b>${esc(q.question)}</b><div class="loc">${esc(q.answer || "unanswered")}</div></div>`);
    return h;
  },
  "change-set-map.json": c => list("Likely files", c.candidate_files, f => `<div class="item"><code>${esc(f.path || f)}</code>${f.reason || f.score != null ? `<div class="loc">${esc(f.reason || "")}${f.score != null ? ` · score ${esc(f.score)}` : ""}</div>` : ""}</div>`) +
    ((c.affected_modules || []).length ? list("Modules", c.affected_modules) : ""),
  "delivery.json": d => {
    const url = d.pr && (d.pr.url || d.pr.compare_url);
    return `<div class="kv"><span>Pull request</span><span>${url ? `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(url)}</a> ${d.pr.draft ? chip("draft") : ""}` : "—"}</span><span>Branch</span><span><code>${esc(d.branch)}</code> → <code>${esc(d.base)}</code></span><span>Commit</span><span><code>${esc((d.sha || "").slice(0, 12))}</code> ${esc(d.subject || "")}</span><span>Pushed</span><span>${esc(d.pushed ?? "—")}</span></div>` +
      list("Files in the commit", d.files) + ((d.warnings || []).length ? list("Warnings", d.warnings, finding) : "");
  },
  "run-state.json": s => `<div class="kv"><span>Status</span><span>${chip(s.status || "?")}</span><span>Now</span><span>${esc(ROLE_OF_STAGE[s.current_stage] || s.current_stage || "—")}</span></div><table><thead><tr><th>Stage</th><th>Status</th><th>Note</th></tr></thead><tbody>${Object.entries(s.stages || {}).map(([k, v]) => `<tr><td>${esc(ROLE_OF_STAGE[k] || k)} <span class="muted">${esc(k)}</span></td><td>${chip(v.status || "?")}</td><td>${esc(v.note || v.reason || "")}</td></tr>`).join("")}</tbody></table>`,
  "stage-metrics.json": m => `<table><thead><tr><th>Stage</th><th>Time</th><th>Tokens</th><th>Attempts</th></tr></thead><tbody>${Object.entries(m.stages || {}).map(([k, v]) => `<tr><td>${esc(ROLE_OF_STAGE[k] || k)}</td><td>${fmtS(v.wall_time_seconds)}</td><td>${fmtT(v.tokens)}</td><td>${esc(v.attempts ?? "—")}</td></tr>`).join("")}</tbody></table>` + (m.totals ? `<div class="kv"><span>Total time</span><span>${fmtS(m.totals.wall_time_seconds)}</span><span>Total tokens</span><span>${fmtT(m.totals.tokens)}</span></div>` : ""),
  "t4-files.json": f => list("Files the Implementer changed", f.files),
  "skills.json": k => {
    const skill = s => `<div class="item"><b>${esc(s.name)}</b> ${chip(s.source === "project" ? "project" : "kit")}${(s.why || []).length ? `<div class="loc">${esc(s.why.join(" · "))}</div>` : ""}${s.description ? `<div class="loc">${esc(s.description)}</div>` : ""}</div>`;
    return `<p class="muted">Chosen by the CLI from the ticket and the code, so nobody has to ask; the app's own skills win over the kit's.</p>`
      + list("Implementer reads", k.implementer, skill) + ((k.device || []).length ? list("Device reads", k.device, skill) : "")
      + `<details><summary>Signals</summary>${Object.entries(k.signals || {}).map(([t, why]) => `<div class="item"><code>${esc(t)}</code> <span class="loc">${esc((why || []).join(" · "))}</span></div>`).join("")}</details>`;
  },
  "prebuild.json": p => `<div class="kv"><span>Status</span><span>${chip(p.status || "?")}</span>${p.reason ? `<span>Reason</span><span>${esc(p.reason)}</span>` : ""}</div>` + list("APKs", p.apks),
};
function renderArtifact(a) {
  if (a.kind === "md") return markdown(a.content);
  if (a.kind === "json") {
    let value;
    try { value = JSON.parse(a.content); } catch (e) { return `<pre>${esc(a.content)}</pre>`; }
    const view = VIEWS[a.name];
    return view ? view(value) + `<details><summary>Raw JSON</summary><pre>${highlight(value)}</pre></details>` : `<pre>${highlight(value)}</pre>`;
  }
  return `<pre>${esc(a.content)}</pre>`;
}
let mediaMode = "side";
try { mediaMode = localStorage.getItem("office.mediaMode") || "side"; } catch (e) { /* storage blocked: keep the default */ }
function compareView(name, before, after) {
  return `<div class="compare" data-name="${esc(name)}"><img class="c-after" src="${esc(after.src)}" alt="${esc(name)} after"><img class="c-before" src="${esc(before.src)}" alt="${esc(name)} before">
    <div class="c-line"></div><div class="c-knob">⇆</div><span class="c-tag l">before</span><span class="c-tag r">after</span>
    <input type="range" min="0" max="100" value="50" step="1" aria-label="Drag to compare before and after: ${esc(name)}"></div><figcaption>${esc(name)} · drag, tap or use the arrow keys</figcaption>`;
}
function bindCompare() {
  document.querySelectorAll(".compare").forEach(box => {
    const input = box.querySelector("input");
    const set = v => { const pos = Math.max(0, Math.min(100, v)); box.style.setProperty("--pos", pos + "%"); input.value = pos; };
    input.addEventListener("input", () => set(Number(input.value)));
    const fromPointer = e => { const r = box.getBoundingClientRect(); set((e.clientX - r.left) / r.width * 100); };
    box.addEventListener("pointerdown", e => { box.setPointerCapture(e.pointerId); fromPointer(e); input.focus({ preventScroll: true }); });
    box.addEventListener("pointermove", e => { if (box.hasPointerCapture(e.pointerId)) fromPointer(e); });
  });
  document.querySelectorAll(".seg button").forEach(b => b.onclick = () => {
    mediaMode = b.dataset.mode;
    try { localStorage.setItem("office.mediaMode", mediaMode); } catch (e) { /* storage blocked */ }
    document.getElementById("dbody").innerHTML = mediaView();
    bindCompare();
  });
}
function mediaView() {
  const m = DATA.media || { before: [], after: [] };
  const names = [...new Set([...m.before, ...m.after].map(x => x.name))];
  if (!names.length) return `<p class="muted">No screenshots or videos yet. The Device agent captures <b>before</b> while the Implementer works and <b>after</b> once the change is reviewed.</p>`;
  const pairOf = name => [m.before.find(x => x.name === name), m.after.find(x => x.name === name)];
  const comparable = names.filter(n => { const [b, a] = pairOf(n); return b && a && b.kind === "image" && a.kind === "image"; });
  const toggle = comparable.length ? `<div class="seg" role="group" aria-label="Comparison mode"><button data-mode="side" class="${mediaMode !== "slider" ? "on" : ""}" aria-pressed="${mediaMode !== "slider"}">Side by side</button><button data-mode="slider" class="${mediaMode === "slider" ? "on" : ""}" aria-pressed="${mediaMode === "slider"}">Slider</button></div>` : "";
  const cell = (phase, name) => {
    const it = m[phase].find(x => x.name === name);
    if (!it) return `<figure><div class="empty">no ${phase}</div></figure>`;
    const media = it.kind === "video" ? `<video src="${esc(it.src)}" controls muted playsinline></video>` : `<a href="${esc(it.src)}" target="_blank"><img src="${esc(it.src)}" alt="${esc(name)} ${phase}"></a>`;
    return `<figure>${media}<figcaption>${phase} · ${esc(name)}</figcaption></figure>`;
  };
  return toggle + names.map(n => {
    const [b, a] = pairOf(n);
    if (mediaMode === "slider" && comparable.includes(n)) return `<div style="margin-bottom:18px">${compareView(n, b, a)}</div>`;
    return `<div class="pair">${cell("before", n)}${cell("after", n)}</div>`;
  }).join("");
}
function changesView(role) {
  const items = changesFor(role), t = now();
  if (!items.length) return `<p class="muted">No file changed yet.</p>`;
  const owners = (DATA.team && DATA.team.owners) || {};
  const intro = role === "Tech Lead" ? "Every file the team's change touches, with the slice that owns it." : "Files this agent is changing, newest edit first. The list updates by itself.";
  return `<p class="muted">${intro}</p>` + items.map(c => {
    const delta = c.status === "??" || c.status === "A" ? `<span class="plus">new${c.added != null ? ` +${c.added}` : ""}</span>` : c.status === "D" ? `<span class="minus">deleted</span>`
      : `<span><span class="plus">+${c.added ?? "?"}</span> <span class="minus">−${c.removed ?? "?"}</span></span>`;
    const owner = role === "Tech Lead" ? `<span class="ago">${esc(owners[c.path] || "unowned")}</span>` : "";
    return `<div class="change ${c.mtime && t - c.mtime < 6 ? "fresh" : ""}"><span class="chip">${esc(c.status === "??" ? "new" : c.status)}</span><code>${esc(c.path)}</code>${delta}${owner || `<span class="ago" ${c.mtime ? `data-ago="${c.mtime}"` : ""}>${c.mtime ? ago(t - c.mtime) : ""}</span>`}</div>`;
  }).join("");
}
function tabsFor(role) {
  const tabs = ((DATA.artifacts || {})[role] || []).map(a => ({ id: a.name, label: a.label, a }));
  if (EDITORS.includes(role) && changesFor(role).length) tabs.unshift({ id: "changes", label: `Live changes (${changesFor(role).length})`, render: () => changesView(role) });
  if (role === "Device") tabs.unshift({ id: "media", label: "Before / after", render: mediaView });
  if (role === "Quality gate" && (DATA.logs || []).length) tabs.push({ id: "logs", label: `Gradle logs (${DATA.logs.length})`, render: () => `<p class="muted">Full Gradle output of each gate run; opens in a new tab.</p>${DATA.logs.map(l => `<div class="item"><a href="${esc(l.src)}" target="_blank">${esc(l.name)}</a> <span class="loc">${fmtB(l.size)}</span></div>`).join("")}` });
  if (role === "Orchestrator") tabs.push({ id: "files", label: `All run files (${(DATA.files || []).length})`, render: () => (DATA.files || []).map(f => `<div class="item"><a href="${esc(f.src)}" target="_blank">${esc(f.rel || f.name)}</a> <span class="loc">${fmtB(f.size)}</span></div>`).join("") });
  return tabs;
}

/* ---------- drawer ---------- */
const drawer = document.getElementById("drawer"), scrim = document.getElementById("scrim");
let refreshTimer = null, OPEN = null;
const drawerOpen = () => drawer.classList.contains("open");
// `refresh`: the run moved while the panel is open. Only the parts that changed are replaced and the
// reader keeps their scroll position (and the before/after slider its place).
function openDrawer(role, tabId, refresh) {
  const desk = deskOf(role), tabs = tabsFor(role), active = tabs.find(t => t.id === tabId) || tabs[0];
  setHTML(document.getElementById("dhead"), `${avatar(role, desk.state)}<div><h3>${esc(role)}</h3><div class="sub">${chip(desk.state || "idle", STATE_LABEL[desk.state] || desk.state || "idle")}<span data-clock="${esc(role)}">${fmtS(liveSeconds(desk))}</span><span>· ${fmtT(desk.tokens)} tokens</span></div></div><button class="close" aria-label="Close">×</button>`);
  setHTML(document.getElementById("dnote"), desk.note ? esc(desk.note) : `<span class="muted">No note from this agent yet.</span>`);
  setHTML(document.getElementById("tabs"), tabs.map(t => `<button class="tab ${t === active ? "on" : ""}" role="tab" aria-selected="${t === active}" data-tab="${esc(t.id)}">${esc(t.label)}</button>`).join(""));
  const body = document.getElementById("dbody"), scroll = body.scrollTop, before = LAST_HTML.get(body);
  setHTML(body, active ? (active.render ? active.render() : renderArtifact(active.a)) : `<p class="muted">This agent has not written anything yet.</p>`);
  const changed = LAST_HTML.get(body) !== before;
  if (!refresh) body.scrollTop = 0; else body.scrollTop = scroll;
  if (changed || !refresh) bindCompare();
  const a = active && active.a;
  setHTML(document.getElementById("dfoot"), (a ? `<a class="btn" href="${esc(a.src)}" target="_blank">Open file ↗</a><button class="btn" id="copy">Copy path</button><span>${esc(a.name)} · ${fmtB(a.size)}${a.truncated ? " · preview truncated" : ""}</span>` : "") + (live() ? `<span class="paused"><span class="livedot"></span>Live</span>` : ""));
  const copy = document.getElementById("copy");
  if (copy && a) copy.onclick = () => navigator.clipboard && navigator.clipboard.writeText(decodeURIComponent(new URL(a.uri).pathname)).then(() => { copy.textContent = "Copied"; });
  document.querySelector("#dhead .close").onclick = closeDrawer;
  document.querySelectorAll(".tab").forEach(b => b.onclick = () => openDrawer(role, b.dataset.tab));
  OPEN = { role, tab: active ? active.id : null };
  if (refresh) return;
  drawer.classList.add("open"); scrim.classList.add("open"); drawer.setAttribute("aria-hidden", "false");
  history.replaceState(null, "", `#agent=${encodeURIComponent(role)}${active ? `&tab=${encodeURIComponent(active.id)}` : ""}`);
  clearTimeout(refreshTimer);
}
function closeDrawer() {
  drawer.classList.remove("open"); scrim.classList.remove("open"); drawer.setAttribute("aria-hidden", "true");
  history.replaceState(null, "", location.pathname);
  OPEN = null;
  scheduleRefresh();
}
// A page with no data script (a past run's own page) falls back to reloading while its run is live.
function scheduleRefresh() { clearTimeout(refreshTimer); if (live() && !DATA.live_src) refreshTimer = setTimeout(() => location.reload(), 3000); }
scrim.onclick = closeDrawer;
document.addEventListener("keydown", e => { if (e.key === "Escape" && drawerOpen()) closeDrawer(); });

/* ---------- live updates: load the data script, redraw in place ---------- */
const keyOf = d => JSON.stringify({ ...d, generated_at: 0 });
let LAST_KEY = keyOf(DATA), pollTimer = null, pollFailures = 0;
function applyData(next) {
  if (!next || typeof next !== "object") return;
  const key = keyOf(next);
  if (key === LAST_KEY) return;
  const newTicket = (next.ticket || null) !== (DATA.ticket || null);
  DATA = next; LAST_KEY = key;
  if (newTicket) { OPEN = null; if (drawerOpen()) closeDrawer(); ROSTER_KEY = ""; }
  renderHeader();
  renderHistory();
  renderSide();
  const roster = rosterOf().join();
  if (roster !== ROSTER_KEY) { ROSTER_KEY = roster; OFFICE = SIM.build(rosterOf()); renderRoom(); }
  else { FURNITURE_ITEMS = null; SCENE_KEY = ""; refreshDeskHits(); drawScene(); }
  if (OPEN && drawerOpen()) openDrawer(OPEN.role, OPEN.tab, true);
  tickClocks();
}
window.officeData = next => { pollFailures = 0; try { applyData(next); } catch (e) { console.error(e); } };
function schedulePoll() { clearTimeout(pollTimer); pollTimer = setTimeout(poll, live() ? 2000 : 6000); }
function poll() {
  if (!DATA.live_src) return;
  const script = document.createElement("script");
  script.src = `${DATA.live_src}?t=${Date.now()}`;
  script.onload = () => { script.remove(); schedulePoll(); };
  script.onerror = () => {
    script.remove();
    // The browser refuses the data script: reload the page instead, as long as no panel is open.
    if (++pollFailures >= 3 && live() && !drawerOpen()) location.reload(); else schedulePoll();
  };
  document.head.appendChild(script);
}

/* ---------- boot ---------- */
function renderHeader() {
  document.getElementById("ticket").textContent = DATA.ticket || "";
  document.getElementById("ticketTitle").textContent = DATA.title || "";
  setHTML(document.getElementById("statusChip"), chip(runStatus()));
  document.title = DATA.ticket ? `${DATA.ticket} · Agent Office` : "Agent Office";
}
function renderHistory() {
  const runs = DATA.history || [];
  const picker = document.getElementById("runPicker"), wrap = document.getElementById("pickerWrap");
  if (runs.length > 1 || (runs.length && !DATA.is_current)) {
    if (document.activeElement !== picker) {
      setHTML(picker, runs.map(r => `<option value="${esc(r.href)}" ${r.ticket === DATA.ticket ? "selected" : ""}>${esc(r.ticket)}${r.current ? " (current)" : ""} · ${esc(r.status)}${r.pr ? (r.draft ? " · draft PR" : " · PR") : ""}${r.updated ? " · " + esc(r.updated) : ""}</option>`).join(""));
    }
    picker.onchange = () => { location.href = picker.value; };
    wrap.hidden = false;
  } else wrap.hidden = true;
  const current = runs.find(r => r.current), bar = document.getElementById("pastbar");
  if (DATA.ticket && !DATA.is_current) {
    setHTML(bar, `You are viewing a past run (${esc(DATA.ticket)}).${current ? ` <a href="${esc(current.href)}">Go to the current run (${esc(current.ticket)}) →</a>` : ""}`);
    bar.hidden = false;
  } else bar.hidden = true;
}
renderHeader();
renderHistory();
renderRoom();
renderSide();
bindSide();
setInterval(() => { if (!document.hidden) tickClocks(); }, 1000);
const hash = new URLSearchParams(location.hash.slice(1));
if (hash.get("agent")) {
  // Reopened after a reload: show the panel at once, no slide-in.
  drawer.classList.add("instant"); scrim.classList.add("instant");
  openDrawer(hash.get("agent"), hash.get("tab"));
  requestAnimationFrame(() => requestAnimationFrame(() => { drawer.classList.remove("instant"); scrim.classList.remove("instant"); }));
} else scheduleRefresh();
poll();
</script>
</body>
</html>
"""
