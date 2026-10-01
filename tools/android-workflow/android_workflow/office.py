"""The office: a pixel-art page that shows the agents of the current run at their desks.

Rewritten after every CLI command into ``TARGET/.ai/workflow/office.html`` from the files the run
already writes, so it costs no tokens and needs no server. While the run is going it reloads itself.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from android_workflow.paths import current_ticket_id, list_runs, workflow_root

OFFICE_NAME = "office.html"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
VIDEO_SUFFIXES = {".mp4", ".webm"}

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


def _timeline(run: Path, limit: int = 14) -> list[dict[str, str]]:
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


def _media(run: Path, root: Path) -> dict[str, list[dict[str, str]]]:
    found: dict[str, list[dict[str, str]]] = {}
    for phase in ("before", "after"):
        folder = run / "media" / phase
        items = []
        if folder.is_dir():
            for path in sorted(folder.iterdir()):
                suffix = path.suffix.lower()
                if suffix in IMAGE_SUFFIXES or suffix in VIDEO_SUFFIXES:
                    kind = "video" if suffix in VIDEO_SUFFIXES else "image"
                    items.append({"name": path.stem, "src": path.relative_to(root).as_posix(), "kind": kind})
        found[phase] = items
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


def collect(target: Path) -> dict[str, Any]:
    root = workflow_root(target)
    ticket = current_ticket_id(target)
    data: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "ticket": ticket,
        "runs": [
            {"ticket": item["ticket_id"], "title": item["title"], "status": item["status"], "current": item["current"]}
            for item in list_runs(target)
        ],
    }
    if not ticket:
        data.update({"status": "idle", "desks": [], "timeline": [], "media": {"before": [], "after": []}})
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
        if stage == "T5" and gate.get("status"):
            note = note or f"gate {gate['status']}"
        desks.append({
            "stage": stage, "role": role, "kind": kind, "colour": colour,
            "state": _desk_state(stage, entry, route, gate, review),
            "note": str(note)[:160],
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
        "media": _media(run, root),
    })
    return data


def render(target: Path) -> Path:
    """Write the office page for TARGET's current run and return its path."""
    data = collect(target)
    live = data.get("status") not in {"completed", "idle"}
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    html = PAGE.replace("__REFRESH__", '<meta http-equiv="refresh" content="3">' if live else "")
    html = html.replace("__DATA__", payload)
    path = office_path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(html, encoding="utf-8")
    temporary.replace(path)
    return path


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
__REFRESH__
<title>Agent Office</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Press+Start+2P&display=swap" rel="stylesheet">
<style>
:root {
  --wall: #2b2d42; --wall-dark: #23253a; --floor-a: #4a3f35; --floor-b: #43392f;
  --ink: #f4f1de; --muted: #b9b4a0; --panel: #1d1e2c; --line: #3c3f58;
  --ok: #5ad17a; --bad: #ff5d5d; --warn: #ffcc4d; --wood: #8a5a35; --wood-dark: #6b4426;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--wall-dark); color: var(--ink);
  font-family: "Press Start 2P", ui-monospace, Menlo, monospace; font-size: 10px; line-height: 1.6; }
header { background: var(--wall); border-bottom: 4px solid #000; padding: 16px; display: flex;
  flex-wrap: wrap; gap: 12px; align-items: center; justify-content: space-between; }
header h1 { font-size: 13px; margin: 0; overflow-wrap: anywhere; }
header > div:first-child { min-width: 0; }
.sub { overflow-wrap: anywhere; }
header .sub { color: var(--muted); margin-top: 6px; }
.pill { display: inline-block; padding: 4px 8px; border: 2px solid #000; background: #3c3f58; }
.pill.running, .pill.awaiting_host { background: #2f6fd6; } .pill.paused { background: #b8860b; }
.pill.escalated { background: #b23a3a; } .pill.completed { background: #2e8b57; }
.wrap { display: grid; grid-template-columns: minmax(0, 1fr) 340px; gap: 0; }
@media (max-width: 900px) { .wrap { grid-template-columns: 1fr; } }
.floor { padding: 24px 16px 32px; min-height: 420px;
  background-color: var(--floor-a);
  background-image: linear-gradient(45deg, var(--floor-b) 25%, transparent 25%, transparent 75%, var(--floor-b) 75%),
    linear-gradient(45deg, var(--floor-b) 25%, transparent 25%, transparent 75%, var(--floor-b) 75%);
  background-size: 32px 32px; background-position: 0 0, 16px 16px; }
.boss { display: flex; justify-content: center; margin-bottom: 18px; }
.desks { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(170px, 100%), 1fr)); gap: 18px; }
@media (max-width: 480px) { .desks { grid-template-columns: 1fr 1fr; gap: 10px; } .floor { padding: 16px 10px; } }
.desk { min-width: 0; position: relative; background: rgba(0,0,0,.28); border: 3px solid #000; padding: 10px 8px 8px;
  text-align: center; min-height: 210px; }
.desk.skipped { opacity: .38; }
.desk.working { box-shadow: 0 0 0 3px var(--warn); }
.desk.failed { box-shadow: 0 0 0 3px var(--bad); }
.desk svg { width: 100%; max-width: 160px; height: auto; aspect-ratio: 36 / 25; image-rendering: pixelated; display: block; margin: 0 auto; }
.name { margin-top: 6px; }
.state { margin-top: 4px; font-size: 8px; color: var(--muted); }
.desk.working .state { color: var(--warn); } .desk.done .state { color: var(--ok); }
.desk.failed .state { color: var(--bad); }
.bubble { position: relative; margin: 8px auto 0; background: var(--ink); color: #111; border: 2px solid #000;
  padding: 6px; font-size: 8px; line-height: 1.5; max-width: 100%; word-break: break-word; text-align: left; }
.bubble:empty { display: none; }
.meta { margin-top: 6px; font-size: 8px; color: var(--muted); }
.badge { position: absolute; top: 6px; right: 8px; font-size: 12px; }
.typing .arm { animation: type .35s steps(2) infinite; }
.typing .arm.r { animation-delay: .17s; }
@keyframes type { 50% { transform: translateY(-1px); } }
.typing .screen { animation: glow 1s steps(2) infinite; }
@keyframes glow { 50% { fill: #b9f3ff; } }
.zzz { animation: float 2s steps(4) infinite; }
@keyframes float { 50% { transform: translateY(-2px); } }
.lamp { animation: blink .6s steps(2) infinite; }
@keyframes blink { 50% { fill: #333; } }
aside { background: var(--panel); border-left: 4px solid #000; padding: 16px; }
aside h2 { font-size: 10px; margin: 18px 0 8px; color: var(--warn); }
aside h2:first-child { margin-top: 0; }
.kv { display: flex; justify-content: space-between; gap: 8px; padding: 3px 0; border-bottom: 1px dashed var(--line); }
.tl { list-style: none; margin: 0; padding: 0; font-size: 8px; }
.tl li { padding: 6px 0; border-bottom: 1px dashed var(--line); }
.tl .t { color: var(--muted); }
.media { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.media figure { margin: 0; } .media img, .media video { width: 100%; max-height: 220px; object-fit: contain; border: 2px solid #000; background: #000; }
.media figcaption { font-size: 7px; color: var(--muted); }
a { color: #7fd1ff; }
.alert { margin: 0 0 16px; padding: 10px; border: 3px solid #000; background: #b8860b; }
.alert.bad { background: #8b2d2d; }
.runs li { font-size: 8px; padding: 3px 0; }
.empty { color: var(--muted); font-size: 8px; }
footer { color: var(--muted); font-size: 7px; padding: 10px 16px; background: var(--wall); border-top: 4px solid #000; }
</style>
</head>
<body>
<header>
  <div><h1 id="title">Agent Office</h1><div class="sub" id="subtitle"></div></div>
  <div id="status"></div>
</header>
<div class="wrap">
  <main class="floor">
    <div id="alerts"></div>
    <div class="boss" id="boss"></div>
    <div class="desks" id="desks"></div>
  </main>
  <aside id="panel"></aside>
</div>
<footer id="footer"></footer>
<script>
const DATA = __DATA__;
const SPRITE = [
  "...hhhh...", "..hhhhhh..", "..hssssh..", "..sesses..", "..ssssss..", "...smms...",
  ".cccccccc.", "cccccccccc", "cccccccccc"];
const PALETTE = { h: "#3b2a20", s: "#f2c39b", e: "#1a1a1a", m: "#c46a5a" };
const LABEL = { working: "working", done: "done", failed: "needs fix", skipped: "not needed",
  waiting: "waiting", idle: "idle" };
const BADGE = { done: "✔", failed: "!", working: "…", waiting: "?" };

function esc(text) {
  return String(text ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
}
function px(x, y, color, cls = "") {
  return `<rect class="${cls}" x="${x}" y="${y}" width="1" height="1" fill="${color}"/>`;
}
function person(colour, x, y) {
  let out = "";
  SPRITE.forEach((row, r) => [...row].forEach((ch, c) => {
    if (ch === ".") return;
    out += px(x + c, y + r, ch === "c" ? colour : PALETTE[ch]);
  }));
  return out;
}
function deskSvg(desk) {
  const working = desk.state === "working";
  let body = `<svg viewBox="2 4 36 25" shape-rendering="crispEdges" class="${working ? "typing" : ""}">`;
  if (desk.kind === "machine") {
    body += `<rect x="12" y="6" width="16" height="18" fill="#9aa4b2"/><rect x="12" y="6" width="16" height="2" fill="#c9d1db"/>`;
    body += `<rect x="15" y="10" width="10" height="5" fill="#13212b"/>`;
    const lamp = desk.state === "done" ? "#5ad17a" : desk.state === "failed" ? "#ff5d5d" : working ? "#ffcc4d" : "#555";
    body += `<rect class="${working ? "lamp" : ""}" x="15" y="18" width="2" height="2" fill="${lamp}"/>`;
    body += `<rect x="19" y="18" width="6" height="1" fill="#6b7480"/><rect x="19" y="20" width="6" height="1" fill="#6b7480"/>`;
    body += `<rect x="10" y="24" width="20" height="3" fill="#6b4426"/>`;
    return body + "</svg>";
  }
  if (desk.state !== "skipped") body += person(desk.colour, 15, 7);
  if (working) {
    body += `<g class="arm">${px(14, 15, desk.colour)}${px(14, 16, PALETTE.s)}</g>`;
    body += `<g class="arm r">${px(25, 15, desk.colour)}${px(25, 16, PALETTE.s)}</g>`;
  }
  if (desk.state === "idle" || desk.state === "waiting") {
    body += `<g class="zzz"><text x="27" y="7" font-size="4" fill="#f4f1de" font-family="monospace">${desk.state === "idle" ? "z" : "?"}</text></g>`;
  }
  body += `<rect x="4" y="17" width="32" height="3" fill="#8a5a35"/><rect x="4" y="20" width="32" height="1" fill="#6b4426"/>`;
  body += `<rect x="6" y="21" width="2" height="8" fill="#6b4426"/><rect x="32" y="21" width="2" height="8" fill="#6b4426"/>`;
  body += `<rect x="7" y="11" width="7" height="6" fill="#222"/><rect class="screen" x="8" y="12" width="5" height="4" fill="${working ? "#6fd3ff" : "#2a3a44"}"/>`;
  body += `<rect x="27" y="15" width="6" height="2" fill="#d9d4c2"/><rect x="28" y="14" width="1" height="1" fill="#2fb36d"/>`;
  return body + "</svg>";
}
function fmtSeconds(value) {
  if (value == null) return "—";
  const s = Math.round(value);
  return s >= 60 ? `${Math.floor(s / 60)}m${String(s % 60).padStart(2, "0")}s` : `${s}s`;
}
function fmtTokens(value) {
  if (value == null) return "—";
  return value >= 1000 ? `${(value / 1000).toFixed(1)}k` : String(value);
}
function deskCard(desk) {
  return `<div class="desk ${desk.state}">
    <span class="badge">${BADGE[desk.state] || ""}</span>
    ${deskSvg(desk)}
    <div class="name">${esc(desk.role)}</div>
    <div class="state">${LABEL[desk.state] || desk.state}</div>
    <div class="bubble">${esc(desk.note)}</div>
    <div class="meta">${fmtSeconds(desk.seconds)} · ${fmtTokens(desk.tokens)} tok</div>
  </div>`;
}
function bossCard() {
  const status = DATA.status || "idle";
  const state = status === "completed" ? "done" : status === "escalated" ? "failed"
    : status === "paused" ? "waiting" : status === "idle" ? "idle" : "working";
  const current = (DATA.desks || []).find(d => d.stage === DATA.current);
  const note = status === "paused" ? `Waiting for your answer: ${DATA.question || ""}`
    : status === "completed" ? (DATA.pr ? "PR delivered" : "Run finished")
    : current ? `Now: ${current.role}` : "";
  return deskCard({ role: "Orchestrator", kind: "agent", colour: "#c9a227", state, note,
    seconds: (DATA.totals || {}).wall_time_seconds, tokens: (DATA.totals || {}).tokens });
}
function panel() {
  let html = "<h2>Ticket</h2>";
  html += `<div class="kv"><span>Status</span><span>${esc(DATA.status)}</span></div>`;
  html += `<div class="kv"><span>Time</span><span>${fmtSeconds((DATA.totals || {}).wall_time_seconds)}</span></div>`;
  html += `<div class="kv"><span>Tokens</span><span>${fmtTokens((DATA.totals || {}).tokens)}</span></div>`;
  if (DATA.pr) html += `<div class="kv"><span>PR</span><span><a href="${esc(DATA.pr)}" target="_blank" rel="noopener">open${DATA.draft.length ? " (draft)" : ""}</a></span></div>`;
  html += "<h2>Timeline</h2>";
  html += DATA.timeline && DATA.timeline.length
    ? `<ul class="tl">${DATA.timeline.slice().reverse().map(r =>
        `<li><span class="t">${esc(r.time)}</span> ${esc(r.role)} · ${esc(r.status)}${r.note ? `<br>${esc(r.note)}` : ""}</li>`).join("")}</ul>`
    : `<div class="empty">Nothing yet.</div>`;
  html += "<h2>Before / after</h2>";
  const media = DATA.media || { before: [], after: [] };
  const names = [...new Set([...media.before, ...media.after].map(m => m.name))];
  html += names.length ? names.map(name => {
    const cell = phase => {
      const item = media[phase].find(m => m.name === name);
      if (!item) return `<figure><div class="empty">no ${phase}</div></figure>`;
      const tag = item.kind === "video" ? `<video src="${esc(item.src)}" controls muted></video>` : `<img src="${esc(item.src)}" alt="${esc(name)} ${phase}">`;
      return `<figure>${tag}<figcaption>${phase} · ${esc(name)}</figcaption></figure>`;
    };
    return `<div class="media">${cell("before")}${cell("after")}</div>`;
  }).join("") : `<div class="empty">No screenshots yet.</div>`;
  const others = (DATA.runs || []).filter(r => !r.current);
  if (others.length) {
    html += `<h2>Other tickets</h2><ul class="tl runs">${others.map(r => `<li>${esc(r.ticket)} · ${esc(r.status || "?")}</li>`).join("")}</ul>`;
  }
  return html;
}
function render() {
  document.getElementById("title").textContent = DATA.ticket ? `Agent Office · ${DATA.ticket}` : "Agent Office";
  document.getElementById("subtitle").textContent = DATA.title || (DATA.ticket ? "" : "No run yet: start /android-workflow in this app.");
  document.getElementById("status").innerHTML = `<span class="pill ${esc(DATA.status)}">${esc(DATA.status || "idle")}</span>`;
  let alerts = "";
  if (DATA.status === "paused") alerts += `<div class="alert">The workflow is waiting for your answer in the chat.</div>`;
  if (DATA.draft && DATA.draft.length) alerts += `<div class="alert bad">Draft PR — still open:<br>${DATA.draft.map(esc).join("<br>")}</div>`;
  document.getElementById("alerts").innerHTML = alerts;
  document.getElementById("boss").innerHTML = DATA.ticket ? bossCard() : "";
  document.getElementById("desks").innerHTML = (DATA.desks || []).map(deskCard).join("");
  document.getElementById("panel").innerHTML = panel();
  document.getElementById("footer").textContent =
    `Updated ${DATA.generated_at}` + (["completed", "idle"].includes(DATA.status) ? "" : " · refreshes every 3s");
}
render();
</script>
</body>
</html>
"""
