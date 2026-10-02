"""The office: an isometric, pixel-art page that shows the agents of the current run at their desks.

Rewritten after every CLI command into ``TARGET/.ai/workflow/office.html`` from the files the run
already writes, so it costs no tokens and needs no server. A browser cannot read sibling files from
a local page, so the readable run files are embedded (size-capped); big Gradle logs are links only.
While the run is going the page reloads itself, except while a details panel is open.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from android_workflow.paths import current_ticket_id, list_runs, workflow_root

OFFICE_NAME = "office.html"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
VIDEO_SUFFIXES = {".mp4", ".webm"}
MAX_EMBED_BYTES = 80_000

# (stage, role, kind, shirt colour): desks in the order the work flows.
DESKS = (
    ("T0", "Setup", "agent", "#8e6bd8"),
    ("T3", "Planner", "agent", "#3d8bfd"),
    ("T4", "Implementer", "agent", "#2fb36d"),
    ("T5", "Quality gate", "machine", "#9aa4b2"),
    ("T6", "Reviewer", "agent", "#e8913a"),
    ("T7", "Device", "agent", "#e2557b"),
    ("T8", "Delivery", "agent", "#18a5a7"),
)
# What each desk opens: (path relative to the run folder, label).
ROLE_FILES = {
    "Orchestrator": (("run-state.json", "Run state"), ("stage-metrics.json", "Time & tokens"),
                     ("stage-log.md", "Stage log")),
    "Setup": (("../../project-profile.md", "Project profile"), ("../../android-workflow.json", "Project overrides")),
    "Planner": (("plan.md", "Plan"), ("ticket-spec.json", "Ticket"), ("change-set-map.json", "Likely files")),
    "Implementer": (("implementation-notes.md", "Notes"), ("t4-files.json", "Changed files")),
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


def _link(path: Path, page_dir: Path) -> dict[str, Any]:
    """A file as the page sees it: `src` is relative to the folder the page is written in."""
    resolved = path.resolve()
    relative = Path(os.path.relpath(resolved, page_dir.resolve())).as_posix()
    return {"name": path.name, "src": relative, "uri": resolved.as_uri(), "size": path.stat().st_size}


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


def collect(target: Path, ticket: str | None = None, page_dir: Path | None = None) -> dict[str, Any]:
    """The page data for one run (the current one by default), with links relative to `page_dir`."""
    root = workflow_root(target)
    current = current_ticket_id(target)
    ticket = ticket or current
    page_dir = (page_dir or root).resolve()
    data: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "ticket": ticket,
        "is_current": ticket == current,
        "history": _history(target, page_dir),
        "live": False,
    }
    if not ticket:
        data.update({"status": "idle", "desks": [], "timeline": [], "media": {"before": [], "after": []},
                     "artifacts": {}, "logs": [], "files": [], "draft": [], "totals": {}})
        return data
    run = root / ticket
    state = _json(run / "run-state.json")
    spec = _json(run / "ticket-spec.json")
    metrics = _json(run / "stage-metrics.json")
    gate = _json(run / "gate-report.json")
    review = _json(run / "review.json")
    delivery = _json(run / "delivery.json")
    stages = state.get("stages") or {}
    route = list(spec.get("route") or [])
    desks = []
    for stage, role, kind, colour in DESKS:
        entry = stages.get(stage) or {}
        measured = (metrics.get("stages") or {}).get(stage) or {}
        note = entry.get("note") or entry.get("reason") or ""
        if stage == "T5" and gate.get("status") and gate["status"] != "not_run":
            note = note or f"gate {gate['status']}"
        desks.append({
            "stage": stage, "role": role, "kind": kind, "colour": colour,
            "state": _desk_state(stage, entry, route, gate, review),
            "note": str(note)[:200],
            "seconds": measured.get("wall_time_seconds"),
            "tokens": measured.get("tokens"),
        })
    totals = dict(metrics.get("totals") or {})
    if not totals:  # mid-run: add up what the stages reported so far
        reported = [item.get("tokens") for item in (metrics.get("stages") or {}).values()
                    if isinstance(item, dict) and isinstance(item.get("tokens"), int)]
        created = state.get("created_at")
        totals = {
            "wall_time_seconds": max(datetime.now(timezone.utc).timestamp() - created, 0) if created else None,
            "tokens": sum(reported) if reported else None,
        }
    pr = delivery.get("pr") or {}
    draft = state.get("draft") or {}
    data.update({
        "title": (spec.get("ticket") or {}).get("title") or "",
        "status": state.get("status") or "idle",
        "current": state.get("current_stage"),
        "question": (state.get("pending_question") or {}).get("question"),
        "draft": draft.get("issues") or [],
        "pr": pr.get("url") or pr.get("compare_url"),
        "totals": totals,
        "desks": desks,
        "timeline": _timeline(run),
        "media": _media(run, page_dir),
        "artifacts": _artifacts(run, page_dir),
        "logs": [_link(path, page_dir) for path in sorted(run.glob("gate-run*.log"))],
        "files": [{**_link(path, page_dir), "rel": path.relative_to(run).as_posix()} for path in sorted(run.rglob("*"))
                  if path.is_file() and "media" not in path.relative_to(run).parts
                  and path.name not in {OFFICE_NAME, f"{Path(OFFICE_NAME).stem}.tmp"}],
        # Only the run that is going right now keeps refreshing; a past run's page is a snapshot.
        "live": ticket == current and (state.get("status") or "idle") not in {"completed", "idle"},
    })
    return data


def _write(path: Path, data: dict[str, Any]) -> None:
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(PAGE.replace("__DATA__", payload), encoding="utf-8")
    temporary.replace(path)


def _stale(page: Path, run: Path) -> bool:
    if not page.exists():
        return True
    built = page.stat().st_mtime
    return any(item.stat().st_mtime > built for item in run.iterdir() if item.is_file() and item != page)


def render(target: Path) -> Path:
    """Write the office page for TARGET's current run, plus one page per run for the history."""
    current = current_ticket_id(target)
    main = office_path(target)
    _write(main, collect(target, current, main.parent))
    for item in list_runs(target):
        run = Path(item["path"])
        page = run / OFFICE_NAME
        if item["ticket_id"] == current or _stale(page, run):
            _write(page, collect(target, item["ticket_id"], run))
    return main


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
html, body { margin: 0; background: var(--bg); color: var(--ink); font-family: var(--sans); font-size: 14px; }
button { font: inherit; color: inherit; }
a { color: var(--info); }
code { font-family: ui-monospace, Menlo, monospace; }
.app { display: grid; grid-template-columns: minmax(0, 1fr) 400px; min-height: 100vh; }
@media (max-width: 1000px) { .app { grid-template-columns: 1fr; } }
.stage { display: flex; flex-direction: column; min-width: 0; }
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
.room-wrap { flex: 1; display: flex; align-items: center; justify-content: center; padding: 12px;
  background: radial-gradient(ellipse at 50% 40%, #232637 0%, #15161f 70%); min-height: 380px; }
.room { width: 100%; max-width: 1100px; height: auto; display: block; }
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
aside.side { background: var(--panel); border-left: 1px solid var(--line); display: flex; flex-direction: column; min-width: 0; max-height: 100vh; position: sticky; top: 0; }
@media (max-width: 1000px) { aside.side { border-left: 0; border-top: 1px solid var(--line); max-height: none; position: static; } }
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
.filebtn .who { font-weight: 600; font-size: 13px; }
.filebtn .what { color: var(--muted); font-size: 11px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.filebtn > div { min-width: 0; }
.avatar { flex: none; width: 28px; height: 28px; border-radius: 8px; display: grid; place-items: center;
  font-family: var(--pixel); font-size: 10px; color: #0b1020; position: relative; }
.avatar .dot { position: absolute; right: -3px; bottom: -3px; width: 10px; height: 10px; border-radius: 50%;
  border: 2px solid var(--panel-2); background: #666; }
.dot.working { background: var(--warn); } .dot.done { background: var(--ok); } .dot.failed { background: var(--bad); }
.dot.waiting { background: #e2a92b; } .dot.skipped { background: #444; }
.feed { flex: 1; overflow: auto; padding: 4px 10px 16px; min-height: 160px; }
.msg { display: flex; gap: 10px; padding: 10px 8px; border-radius: 10px; cursor: pointer; }
.msg:hover, .msg:focus-visible { background: var(--panel-2); outline: none; }
.msg .body { min-width: 0; flex: 1; }
.msg .head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; font-size: 12px; }
.msg .head b { font-size: 13px; }
.msg .time { color: var(--muted); font-size: 11px; margin-left: auto; }
.msg .text { margin-top: 4px; color: #d6d9e6; font-size: 13px; line-height: 1.45; overflow-wrap: anywhere; }
.msg .chip { padding: 1px 8px; font-size: 11px; }
.empty { color: var(--muted); font-size: 13px; padding: 8px; }
.run { display: flex; align-items: center; gap: 10px; padding: 8px; border-radius: 10px; text-decoration: none; color: var(--ink); }
.run:hover, .run:focus-visible { background: var(--panel-2); outline: none; }
.run.on { background: var(--panel-2); border: 1px solid var(--line); }
.run .meta { min-width: 0; flex: 1; }
.run .meta div:first-child { font-weight: 600; font-size: 13px; }
.run .meta div:last-child { color: var(--muted); font-size: 11px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.run .chip { padding: 1px 8px; font-size: 11px; }
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
    <div class="section" id="runsSection" hidden><h2>Tickets in this app</h2><div id="runs"></div></div>
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
const DATA = __DATA__;
const ROLE_OF_STAGE = { T0: "Setup", T1: "Planner", T2: "Planner", T3: "Planner", T4: "Implementer",
  T5: "Quality gate", T6: "Reviewer", T7: "Device", T8: "Delivery", T9: "Orchestrator" };
const ROLE_ALIAS = { Triage: "Planner", Localizer: "Planner", Telemetry: "Orchestrator", Bootstrap: "Orchestrator" };
const LOOK = { Orchestrator: ["#2a1d14", "#f1c27d"], Setup: ["#c24d2c", "#e0ac69"], Planner: ["#1d1d1d", "#f6d1b0"],
  Implementer: ["#6b3e1e", "#c68642"], Reviewer: ["#e7c35a", "#f6d1b0"], Device: ["#3a2a5a", "#8d5524"], Delivery: ["#111", "#ffdbac"] };
const STATE_LABEL = { working: "working", done: "done", failed: "needs a fix", skipped: "not needed", waiting: "waiting", idle: "idle" };
const ROLES = ["Orchestrator", "Setup", "Planner", "Implementer", "Quality gate", "Reviewer", "Device", "Delivery"];

const esc = t => String(t ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const fmtS = v => { if (v == null) return "—"; const s = Math.round(v); return s >= 3600 ? `${Math.floor(s / 3600)}h${String(Math.floor(s % 3600 / 60)).padStart(2, "0")}m` : s >= 60 ? `${Math.floor(s / 60)}m${String(s % 60).padStart(2, "0")}s` : `${s}s`; };
const fmtT = v => v == null ? "—" : v >= 1e6 ? `${(v / 1e6).toFixed(1)}M` : v >= 1000 ? `${(v / 1000).toFixed(1)}k` : String(v);
const fmtB = v => v == null ? "" : v >= 1e6 ? `${(v / 1e6).toFixed(1)} MB` : v >= 1000 ? `${Math.round(v / 1000)} KB` : `${v} B`;
const live = () => Boolean(DATA.live);
const chip = (s, label) => `<span class="chip ${esc(String(s).toLowerCase().split(" ")[0])}">${esc(label || s)}</span>`;

function bossDesk() {
  const s = DATA.status || "idle";
  const state = s === "completed" ? "done" : s === "escalated" ? "failed" : s === "paused" ? "waiting" : s === "idle" ? "idle" : "working";
  const current = (DATA.desks || []).find(d => d.stage === DATA.current);
  const note = s === "paused" ? `Waiting for your answer: ${DATA.question || ""}`
    : s === "completed" ? (DATA.pr ? ((DATA.draft || []).length ? "Draft PR delivered" : "PR delivered") : "Run finished")
    : current ? `Coordinating · now ${current.role}` : "";
  return { stage: "boss", role: "Orchestrator", kind: "agent", colour: "#d4a72c", state, note,
    seconds: (DATA.totals || {}).wall_time_seconds, tokens: (DATA.totals || {}).tokens };
}
const desks = () => DATA.ticket ? [bossDesk(), ...(DATA.desks || [])] : [];
const deskOf = role => desks().find(d => d.role === role) || { role, colour: role === "Orchestrator" ? "#d4a72c" : "#888", state: "idle" };

/* ---------- isometric room ---------- */
const TW = 56, TH = 28, W = 12, D = 10, WALL = 112, PAD = 26;
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
function floorTiles() {
  let out = "";
  for (let x = 0; x < W; x++) for (let y = 0; y < D; y++) {
    out += `<polygon points="${pts([iso(x, y), iso(x + 1, y), iso(x + 1, y + 1), iso(x, y + 1)])}" fill="${(x + y) % 2 ? "#c9b896" : "#d6c7a5"}" stroke="#b9a885" stroke-width=".6"/>`;
  }
  return out + poly([iso(5.0, 0.5), iso(9.2, 0.5), iso(9.2, 2.6), iso(5.0, 2.6)], "#7a4e9c", 'opacity=".5"');
}
function walls() {
  const H = WALL;
  let out = poly([iso(0, 0), iso(W, 0), iso(W, 0, H), iso(0, 0, H)], "#8fa9c8");
  out += poly([iso(0, 0), iso(0, D), iso(0, D, H), iso(0, 0, H)], "#7591b4");
  out += poly([iso(0, 0, H), iso(W, 0, H), iso(W, -0.25, H), iso(0, -0.25, H)], "#5b6f8c");
  out += poly([iso(0, 0, H), iso(0, D, H), iso(-0.25, D, H), iso(-0.25, 0, H)], "#4f6280");
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
  // text sheared onto the back wall (slope TH/TW = 0.5)
  out += `<g transform="matrix(1 0.5 0 1 ${bx} ${by})"><text x="0" y="0" font-size="7.5" fill="#e8f1df" text-anchor="middle">${esc((DATA.ticket || "NO RUN").slice(0, 10))}</text></g>`;
  out += poly([iso(0, 2.2, 88), iso(0, 3.6, 88), iso(0, 3.6, 48), iso(0, 2.2, 48)], "#ffcf5a");
  out += poly([iso(0, 2.45, 80), iso(0, 3.35, 80), iso(0, 3.35, 58), iso(0, 2.45, 58)], "#3d8bfd");
  const [cx, cy] = iso(0, 6, 76);
  out += `<g transform="matrix(1 -0.5 0 1 ${cx} ${cy})"><ellipse cx="0" cy="0" rx="10" ry="10" fill="#f4f1de" stroke="#222" stroke-width="1.5"/><path d="M0 0 V-6 M0 0 H4" stroke="#222" stroke-width="1.4"/></g>`;
  return out;
}
function plant(x, y) {
  const [px, py] = iso(x + 0.22, y + 0.22, 14);
  return box(x, y, 0, 0.45, 0.45, 14, "#b5643a") + `<ellipse cx="${px}" cy="${py - 10}" rx="13" ry="12" fill="#3f9b4b" stroke="rgba(0,0,0,.3)"/><ellipse cx="${px - 5}" cy="${py - 16}" rx="7" ry="7" fill="#57b864"/>`;
}
function cooler(x, y) {
  const [px, py] = iso(x + 0.25, y + 0.25, 26);
  return box(x, y, 0, 0.5, 0.5, 26, "#e8e8ee") + `<ellipse cx="${px}" cy="${py - 9}" rx="8" ry="10" fill="#8fd3ff" stroke="rgba(0,0,0,.3)"/>`;
}
const SPRITE = ["....hhhh....", "...hhhhhhh..", "..hhhhhhhhh.", "..hhsssssh..", "...ssesses..", "...ssssss...", "....smms....",
  ".....ss.....", "..cccccccc..", ".cccccccccc.", ".cccccccccc.", ".cccccccccc.", "..cccccccc.."];
function person(desk, ax, ay, s = 2.3) {
  const [hair, skin] = LOOK[desk.role] || ["#3b2a20", "#f2c39b"];
  const pal = { h: hair, s: skin, e: "#1a1a1a", m: "#c46a5a", c: desk.colour };
  const w = SPRITE[0].length * s, h = SPRITE.length * s, x0 = ax - w / 2, y0 = ay - h;
  let out = `<g shape-rendering="crispEdges">`;
  SPRITE.forEach((row, r) => [...row].forEach((ch, c) => {
    if (ch !== ".") out += `<rect x="${(x0 + c * s).toFixed(1)}" y="${(y0 + r * s).toFixed(1)}" width="${(s + .2).toFixed(1)}" height="${(s + .2).toFixed(1)}" fill="${pal[ch]}"/>`;
  }));
  const hand = (cx, cls) => `<g class="arm ${cls}"><rect x="${cx.toFixed(1)}" y="${(y0 + 10 * s).toFixed(1)}" width="${(s * 1.2).toFixed(1)}" height="${(s * 2.4).toFixed(1)}" fill="${desk.colour}"/><rect x="${cx.toFixed(1)}" y="${(y0 + 12.4 * s).toFixed(1)}" width="${(s * 1.2).toFixed(1)}" height="${(s * 1.1).toFixed(1)}" fill="${skin}"/></g>`;
  out += hand(x0 - s * 0.2, "l") + hand(x0 + w - s, "r");
  return { svg: out + "</g>", top: y0 };
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
};
function station(desk, tx, ty, wide) {
  const parts = [], working = desk.state === "working", skipped = desk.state === "skipped", dim = skipped ? 'opacity=".42"' : "";
  const ring = [iso(tx - 0.2, ty - 1.05), iso(tx + wide + 0.2, ty - 1.05), iso(tx + wide + 0.2, ty + 1.2), iso(tx - 0.2, ty + 1.2)];
  parts.push({ d: -50, s: `<polygon class="ring" data-ring="${esc(desk.role)}" points="${pts(ring)}"/>` });
  if (desk.kind === "machine") {
    const lamp = desk.state === "done" ? "#4fd07d" : desk.state === "failed" ? "#ff6262" : working ? "#ffc94d" : "#555";
    let m = box(tx + 0.3, ty - 0.2, 0, 1.1, 0.9, 54, "#9aa4b2", dim);
    m += poly([iso(tx + 0.42, ty + 0.7, 46), iso(tx + 1.28, ty + 0.7, 46), iso(tx + 1.28, ty + 0.7, 30), iso(tx + 0.42, ty + 0.7, 30)], working ? "#1d4a5e" : "#13212b", working ? 'class="screen-on"' : "");
    m += poly([iso(tx + 0.5, ty + 0.7, 24), iso(tx + 0.66, ty + 0.7, 24), iso(tx + 0.66, ty + 0.7, 18), iso(tx + 0.5, ty + 0.7, 18)], lamp, working ? 'class="lamp-on"' : "");
    for (let i = 0; i < 3; i++) m += poly([iso(tx + 0.8, ty + 0.7, 24 - i * 4), iso(tx + 1.25, ty + 0.7, 24 - i * 4), iso(tx + 1.25, ty + 0.7, 22.6 - i * 4), iso(tx + 0.8, ty + 0.7, 22.6 - i * 4)], "#5d6673");
    parts.push({ d: tx + ty + 1.5, s: m });
    const [hx, hy] = iso(tx + 0.85, ty + 0.25, 54);
    let over = tag(hx, hy - 8, desk) + statusMark(hx + desk.role.length * 3 + 17, hy - 14, desk);
    if (desk.state === "failed" && desk.note) over += bubble(hx, hy - 24, desk.note, true);
    if (working && desk.note) over += bubble(hx, hy - 24, desk.note);
    parts.push({ d: 99, s: over });
    return { parts, hit: [hx, hy + 30] };
  }
  parts.push({ d: tx + ty - 0.3, s: box(tx + wide / 2 - 0.25, ty - 0.75, 0, 0.5, 0.45, 10, "#454a5c", dim) + box(tx + wide / 2 - 0.25, ty - 0.82, 10, 0.5, 0.08, 18, "#3a3e4e", dim) });
  const [ax, ay] = iso(tx + wide / 2 + 0.05, ty - 0.35, 12);
  let top = ay - 30;
  if (!skipped) {
    const p = person(desk, ax, ay + 2);
    top = p.top;
    parts.push({ d: tx + ty + 0.1, s: `<g class="${working ? "typing" : ""}">${p.svg}</g>` });
  }
  let d = box(tx, ty, 0, wide, 0.9, 17, desk.role === "Orchestrator" ? "#6d3f22" : "#9a6a3f", dim);
  d += box(tx + 0.2, ty + 0.25, 17, 0.62, 0.14, 15, "#23262f", dim);
  d += poly([iso(tx + 0.26, ty + 0.39, 30), iso(tx + 0.76, ty + 0.39, 30), iso(tx + 0.76, ty + 0.39, 19), iso(tx + 0.26, ty + 0.39, 19)], working ? "#7fe0ff" : "#2c3b47", working ? 'class="screen-on"' : "");
  if (working) for (let i = 0; i < 3; i++) {
    const end = tx + 0.51 + (i % 2) * 0.15;
    d += poly([iso(tx + 0.31, ty + 0.39, 27 - i * 3), iso(end, ty + 0.39, 27 - i * 3), iso(end, ty + 0.39, 26 - i * 3), iso(tx + 0.31, ty + 0.39, 26 - i * 3)], "#0f3a4a");
  }
  if (EXTRAS[desk.role] && !skipped) d += EXTRAS[desk.role](tx, ty, wide, working);
  parts.push({ d: tx + ty + wide + 0.9, s: d });
  const above = Math.min(top, ay - 30) - 6;
  const floating = ["idle", "waiting"].includes(desk.state);
  let over = tag(ax, above, desk) + (floating ? statusMark(ax, above - 20, desk) : statusMark(ax + desk.role.length * 3 + 17, above - 6, desk));
  if (working && desk.note) over += bubble(ax, above - 16, desk.note);
  if (desk.state === "failed" && desk.note) over += bubble(ax, above - 16, desk.note, true);
  parts.push({ d: 99, s: over });
  return { parts, hit: [ax, ay] };
}
const LAYOUT = { Orchestrator: [5.6, 1.4, 2.4], Setup: [1.0, 3.9, 2], Planner: [4.4, 3.9, 2], Implementer: [7.8, 3.9, 2],
  "Quality gate": [0.8, 7.4, 2], Reviewer: [3.2, 7.6, 2], Device: [6.1, 7.6, 2], Delivery: [9.0, 7.6, 2] };
function renderRoom() {
  const svg = document.getElementById("room");
  svg.setAttribute("viewBox", `0 0 ${VW} ${VH}`);
  const layers = [{ d: -100, s: walls() + floorTiles() }, { d: 0.8, s: plant(0.3, 0.3) }, { d: 11.6, s: plant(11.3, 0.3) },
    { d: 13.6, s: cooler(11.2, 2.4) }, { d: 21, s: plant(11.3, 9.3) }];
  const hits = [];
  for (const desk of desks()) {
    const [tx, ty, wide] = LAYOUT[desk.role];
    const st = station(desk, tx, ty, wide);
    layers.push(...st.parts);
    const [hx, hy] = st.hit;
    hits.push(`<g class="spot" data-role="${esc(desk.role)}" tabindex="0" role="button" aria-label="${esc(desk.role)}: ${esc(STATE_LABEL[desk.state] || desk.state)}. Open details."><rect class="hit" x="${(hx - 64).toFixed(1)}" y="${(hy - 96).toFixed(1)}" width="128" height="132" rx="10"/></g>`);
  }
  layers.sort((a, b) => a.d - b.d);
  svg.innerHTML = layers.map(l => l.s).join("") + hits.join("");
  svg.querySelectorAll(".spot").forEach(el => {
    const ring = svg.querySelector(`.ring[data-ring="${CSS.escape(el.dataset.role)}"]`);
    const on = () => ring && ring.classList.add("hover"), off = () => ring && ring.classList.remove("hover");
    el.addEventListener("mouseenter", on); el.addEventListener("mouseleave", off);
    el.addEventListener("focus", on); el.addEventListener("blur", off);
    el.addEventListener("click", () => openDrawer(el.dataset.role));
    el.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openDrawer(el.dataset.role); } });
  });
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
    html = `<div class="stats"><div class="stat"><span>Status</span><b>${chip(DATA.status || "idle")}</b></div><div class="stat"><span>Time</span><b>${fmtS(t.wall_time_seconds)}</b></div><div class="stat"><span>Tokens</span><b>${fmtT(t.tokens)}</b></div></div>`;
    if (DATA.pr) html += `<div class="alert ${(DATA.draft || []).length ? "warn" : "ok"}"><b>${(DATA.draft || []).length ? "Draft PR" : "Pull request"}:</b> <a href="${esc(DATA.pr)}" target="_blank" rel="noopener">${esc(DATA.pr)}</a></div>`;
    if (DATA.status === "paused") html += `<div class="alert warn"><b>Waiting for you in the chat.</b><br>${esc(DATA.question || "")}</div>`;
    if ((DATA.draft || []).length) html += `<div class="alert bad"><b>Still open:</b><ul>${DATA.draft.map(i => `<li>${esc(i)}</li>`).join("")}</ul></div>`;
  }
  document.getElementById("summary").innerHTML = html;
  document.getElementById("files").innerHTML = DATA.ticket ? ROLES.map(role => {
    const desk = deskOf(role), files = (DATA.artifacts || {})[role] || [];
    const media = (DATA.media.before || []).length + (DATA.media.after || []).length;
    const bits = files.map(f => f.label);
    if (role === "Device" && media) bits.unshift(`${media} screenshot(s)`);
    if (role === "Quality gate" && (DATA.logs || []).length) bits.push(`${DATA.logs.length} log(s)`);
    return `<button class="filebtn" data-role="${esc(role)}">${avatar(role, desk.state)}<div><div class="who">${esc(role)}</div><div class="what">${esc(bits.join(" · ") || "nothing yet")}</div></div></button>`;
  }).join("") : "";
  document.querySelectorAll(".filebtn").forEach(b => b.addEventListener("click", () => openDrawer(b.dataset.role)));
  const rows = (DATA.timeline || []).slice().reverse();
  document.getElementById("feed").innerHTML = rows.length ? rows.map(r => {
    const role = ROLE_ALIAS[r.role] || r.role;
    return `<div class="msg" tabindex="0" data-role="${esc(role)}">${avatar(role)}<div class="body"><div class="head"><b>${esc(r.role)}</b>${chip(r.status)}<span class="time">${esc(r.time)}</span></div>${r.note ? `<div class="text">${esc(r.note)}</div>` : ""}</div></div>`;
  }).join("") : `<div class="empty">Nothing has happened yet.</div>`;
  document.querySelectorAll(".msg").forEach(m => {
    m.addEventListener("click", () => openDrawer(m.dataset.role));
    m.addEventListener("keydown", e => { if (e.key === "Enter") openDrawer(m.dataset.role); });
  });
  document.getElementById("foot").textContent = `Updated ${DATA.generated_at}` + (live() ? " · refreshes every 3s" : "");
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
function tabsFor(role) {
  const tabs = ((DATA.artifacts || {})[role] || []).map(a => ({ id: a.name, label: a.label, a }));
  if (role === "Device") tabs.unshift({ id: "media", label: "Before / after", render: mediaView });
  if (role === "Quality gate" && (DATA.logs || []).length) tabs.push({ id: "logs", label: `Gradle logs (${DATA.logs.length})`, render: () => `<p class="muted">Full Gradle output of each gate run; opens in a new tab.</p>${DATA.logs.map(l => `<div class="item"><a href="${esc(l.src)}" target="_blank">${esc(l.name)}</a> <span class="loc">${fmtB(l.size)}</span></div>`).join("")}` });
  if (role === "Orchestrator") tabs.push({ id: "files", label: `All run files (${(DATA.files || []).length})`, render: () => (DATA.files || []).map(f => `<div class="item"><a href="${esc(f.src)}" target="_blank">${esc(f.rel || f.name)}</a> <span class="loc">${fmtB(f.size)}</span></div>`).join("") });
  return tabs;
}

/* ---------- drawer ---------- */
const drawer = document.getElementById("drawer"), scrim = document.getElementById("scrim");
let refreshTimer = null;
function openDrawer(role, tabId) {
  const desk = deskOf(role), tabs = tabsFor(role), active = tabs.find(t => t.id === tabId) || tabs[0];
  document.getElementById("dhead").innerHTML = `${avatar(role, desk.state)}<div><h3>${esc(role)}</h3><div class="sub">${chip(desk.state || "idle", STATE_LABEL[desk.state] || desk.state || "idle")}<span>${fmtS(desk.seconds)}</span><span>· ${fmtT(desk.tokens)} tokens</span></div></div><button class="close" aria-label="Close">×</button>`;
  document.getElementById("dnote").innerHTML = desk.note ? esc(desk.note) : `<span class="muted">No note from this agent yet.</span>`;
  document.getElementById("tabs").innerHTML = tabs.map(t => `<button class="tab ${t === active ? "on" : ""}" role="tab" aria-selected="${t === active}" data-tab="${esc(t.id)}">${esc(t.label)}</button>`).join("");
  document.getElementById("dbody").innerHTML = active ? (active.render ? active.render() : renderArtifact(active.a)) : `<p class="muted">This agent has not written anything yet.</p>`;
  document.getElementById("dbody").scrollTop = 0;
  bindCompare();
  const a = active && active.a;
  document.getElementById("dfoot").innerHTML = (a ? `<a class="btn" href="${esc(a.src)}" target="_blank">Open file ↗</a><button class="btn" id="copy">Copy path</button><span>${esc(a.name)} · ${fmtB(a.size)}${a.truncated ? " · preview truncated" : ""}</span>` : "") + (live() ? `<span class="paused">Live updates paused while this panel is open</span>` : "");
  const copy = document.getElementById("copy");
  if (copy && a) copy.onclick = () => navigator.clipboard && navigator.clipboard.writeText(decodeURIComponent(new URL(a.uri).pathname)).then(() => { copy.textContent = "Copied"; });
  document.querySelector("#dhead .close").onclick = closeDrawer;
  document.querySelectorAll(".tab").forEach(b => b.onclick = () => openDrawer(role, b.dataset.tab));
  drawer.classList.add("open"); scrim.classList.add("open"); drawer.setAttribute("aria-hidden", "false");
  history.replaceState(null, "", `#agent=${encodeURIComponent(role)}${active ? `&tab=${encodeURIComponent(active.id)}` : ""}`);
  clearTimeout(refreshTimer);
}
function closeDrawer() {
  drawer.classList.remove("open"); scrim.classList.remove("open"); drawer.setAttribute("aria-hidden", "true");
  history.replaceState(null, "", location.pathname);
  scheduleRefresh();
}
function scheduleRefresh() { clearTimeout(refreshTimer); if (live()) refreshTimer = setTimeout(() => location.reload(), 3000); }
scrim.onclick = closeDrawer;
document.addEventListener("keydown", e => { if (e.key === "Escape" && drawer.classList.contains("open")) closeDrawer(); });

/* ---------- boot ---------- */
document.getElementById("ticket").textContent = DATA.ticket || "";
document.getElementById("ticketTitle").textContent = DATA.title || "";
document.getElementById("statusChip").innerHTML = chip(DATA.status || "idle");
document.title = DATA.ticket ? `${DATA.ticket} · Agent Office` : "Agent Office";
function renderHistory() {
  const runs = DATA.history || [];
  if (runs.length > 1 || (runs.length && !DATA.is_current)) {
    const picker = document.getElementById("runPicker");
    picker.innerHTML = runs.map(r => `<option value="${esc(r.href)}" ${r.ticket === DATA.ticket ? "selected" : ""}>${esc(r.ticket)}${r.current ? " (current)" : ""} · ${esc(r.status)}${r.updated ? " · " + esc(r.updated) : ""}</option>`).join("");
    picker.onchange = () => { location.href = picker.value; };
    document.getElementById("pickerWrap").hidden = false;
  }
  if (runs.length > 1) {
    document.getElementById("runs").innerHTML = runs.map(r => `<a class="run ${r.ticket === DATA.ticket ? "on" : ""}" href="${esc(r.href)}">
      <div class="meta"><div>${esc(r.ticket)}${r.current ? ' <span class="muted">· current</span>' : ""}</div><div>${esc(r.title || "")}${r.updated ? " · " + esc(r.updated) : ""}</div></div>
      ${r.pr ? `<span class="chip ${r.draft ? "draft" : "passed"}" title="${esc(r.pr)}">${r.draft ? "draft PR" : "PR"}</span>` : ""}${chip(r.status)}</a>`).join("");
    document.getElementById("runsSection").hidden = false;
  }
  const current = runs.find(r => r.current);
  if (DATA.ticket && !DATA.is_current) {
    const bar = document.getElementById("pastbar");
    bar.innerHTML = `You are viewing a past run (${esc(DATA.ticket)}).${current ? ` <a href="${esc(current.href)}">Go to the current run (${esc(current.ticket)}) →</a>` : ""}`;
    bar.hidden = false;
  }
}
renderHistory();
renderRoom();
renderSide();
const hash = new URLSearchParams(location.hash.slice(1));
if (hash.get("agent")) {
  // Reopened after a reload: show the panel at once, no slide-in.
  drawer.classList.add("instant"); scrim.classList.add("instant");
  openDrawer(hash.get("agent"), hash.get("tab"));
  requestAnimationFrame(() => requestAnimationFrame(() => { drawer.classList.remove("instant"); scrim.classList.remove("instant"); }));
} else scheduleRefresh();
</script>
</body>
</html>
"""
