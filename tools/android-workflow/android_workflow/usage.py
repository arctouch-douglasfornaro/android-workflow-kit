"""Tokens an agent has used so far, read from the host's own transcript, while it is still working.

An agent cannot count its own tokens, and the orchestrator only learns the total when the agent
returns. The hosts already write every request's usage to disk, so the office (and `log`, when the
orchestrator passes no `--tokens`) reads it from there. One adapter per host, one shared rule:

- **Which agent**: the transcript names the run (`RUN=<path>` in its first prompt, Claude Code) or
  works in the app's folder (`cwd`, Codex), and its role comes from the host's agent type
  (`aw-<role>`) or the agent's name (Codex: `aw_<role>[_s<n>]`), or from an `AW_STAGE=<Role>[/S<n>]`
  line in the prompt where the host keeps the prompt readable.
- **Which number**: the same one the host reports when the agent returns, so the live figure turns
  into the final one without a jump: the tokens of the agent's latest request (its whole context
  plus what it wrote). Claude Code: `usage` of the last assistant message. Codex: `last_token_usage`
  of the last `token_count` event.

Cursor and Gemini write no per-agent token log that can be read: they keep the reported total only.
Files are read incrementally (only what was appended since the last tick), and never written.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

ROLE_STAGES = (
    ("watcher", "T10"),
    ("tech_lead", "T4L"), ("tech-lead", "T4L"), ("techlead", "T4L"), ("tech lead", "T4L"),
    ("setup", "T0"), ("planner", "T3"), ("implementer", "T4"), ("reviewer", "T6"),
    ("device", "T7"), ("delivery", "T8"),
)
MARKER = re.compile(r"AW_STAGE=([A-Za-z][A-Za-z _-]*?)(?:/(S[1-9]))?(?=\s|$|[\\\"',;])")
TWO_WORDS = re.compile(r"([A-Za-z][A-Za-z_-]* [A-Za-z][A-Za-z_-]*?)(?:/(S[1-9]))?(?=\s|$|[\\\"',;])")
SLICE_TEXT = re.compile(r"(?:--slice|\bslice)\s+(S[1-9])\b", re.IGNORECASE)
SLICE_NAME = re.compile(r"(?:^|[_\-\s])s([1-9])$", re.IGNORECASE)
CODEX_HEAD_LINES = 400  # where a Codex child's own task (after the forked history) starts


def stage_of(name: str | None) -> str | None:
    """`aw-planner`, `/root/reviewer_delta`, `Implementer` → its stage id."""
    text = str(name or "").strip().lower()
    for word, stage in ROLE_STAGES:
        if word in text:
            return stage
    return None


def _marker(text: str) -> tuple[str | None, str | None]:
    found = MARKER.search(text)
    if not found:
        return None, None
    stage = stage_of(found.group(1))
    if stage is None:  # a two-word role: `AW_STAGE=PR Watcher`, `AW_STAGE=Tech Lead/S2`
        pair = TWO_WORDS.match(text, found.start(1))
        if pair:
            return stage_of(pair.group(1)), pair.group(2) or found.group(2)
    return stage, found.group(2)


def _epoch(value: Any) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


@dataclass
class Transcript:
    host: str
    path: Path
    stage: str | None = None
    slice_id: str | None = None
    bound: bool | None = None  # None: not decided yet (the first prompt was not written yet)
    records: list[tuple[float, int]] = field(default_factory=list)  # (when, tokens of that request)
    offset: int = 0
    lines: int = 0
    size: int = 0
    mtime: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)


# Kept between ticks of the watcher (one process): a transcript is parsed once, then only its tail.
_CACHE: dict[str, Transcript] = {}


def _claude_home() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def _codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(item.get("text") or "") for item in content if isinstance(item, dict))
    return ""


def _names_run(text: str, target: Path, run: Path) -> bool:
    """`RUN=<path>`, or `RUN=TARGET/.ai/workflow/<ticket>` next to `TARGET=<path>`."""
    if str(run) in text:
        return True
    return f".ai/workflow/{run.name}" in text and str(target) in text


def _claude_line(item: Transcript, obj: dict[str, Any], target: Path, run: Path) -> None:
    message = obj.get("message") or {}
    if item.bound is None and obj.get("type") == "user":
        # The orchestrator's prompt: it names the run, and maybe the role and slice.
        text = _text(message.get("content"))
        item.bound = _names_run(text, target, run)
        stage, slice_id = _marker(text)
        item.stage = stage or item.stage
        found = SLICE_TEXT.search(text)
        item.slice_id = slice_id or (found.group(1).upper() if found else None)
        return
    usage = message.get("usage")
    when = _epoch(obj.get("timestamp"))
    if obj.get("type") == "assistant" and isinstance(usage, dict) and when is not None:
        total = sum(int(usage.get(key) or 0) for key in (
            "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens"))
        if total:
            item.records.append((when, total))


def _codex_line(item: Transcript, obj: dict[str, Any], target: Path) -> None:
    payload = obj.get("payload") or {}
    kind = obj.get("type")
    if item.bound is None and kind == "session_meta":
        spawn = ((payload.get("source") or {}).get("subagent") or {}) if isinstance(payload.get("source"), dict) else {}
        spawn = spawn.get("thread_spawn") if isinstance(spawn, dict) else None
        cwd = payload.get("cwd")
        if not isinstance(spawn, dict) or not cwd:
            item.bound = False  # the orchestrator's own thread, or another tool's session
            return
        try:
            item.bound = Path(cwd).resolve() == target
        except OSError:
            item.bound = False
        name = spawn.get("agent_role") or payload.get("agent_role") or spawn.get("agent_path") or payload.get("agent_path")
        item.stage = stage_of(name)
        short = str(spawn.get("agent_path") or payload.get("agent_path") or "").rstrip("/").rsplit("/", 1)[-1]
        found = SLICE_NAME.search(short)
        item.slice_id = f"S{found.group(1)}" if found else None
        item.extra["start"] = int(payload.get("subagent_history_start_ordinal") or 0)
        return
    ordinal = obj.get("ordinal")
    own = not isinstance(ordinal, int) or ordinal >= item.extra.get("start", 0)  # not the forked history
    if not own:
        return
    if kind == "response_item" and item.lines < CODEX_HEAD_LINES and not item.extra.get("marked"):
        stage, slice_id = _marker(_text(payload.get("content")))
        if stage:
            item.stage, item.slice_id, item.extra["marked"] = stage, slice_id, True
    if kind == "event_msg" and payload.get("type") == "token_count":
        last = ((payload.get("info") or {}).get("last_token_usage") or {}).get("total_tokens")
        when = _epoch(obj.get("timestamp"))
        if isinstance(last, int) and last and when is not None:
            item.records.append((when, last))


def _read(item: Transcript, parse) -> None:
    """Parse only what was appended since the last read (whole lines only)."""
    try:
        stat = item.path.stat()
    except OSError:
        return
    if stat.st_size < item.offset:
        item.offset, item.records, item.bound, item.lines = 0, [], None, 0  # rewritten: start over
    elif stat.st_size == item.size and stat.st_mtime == item.mtime:
        return
    try:
        with item.path.open("rb") as handle:
            handle.seek(item.offset)
            chunk = handle.read()
    except OSError:
        return
    end = chunk.rfind(b"\n")
    if end < 0:
        return
    for raw in chunk[:end].splitlines():
        item.lines += 1
        if item.bound is False:
            break
        try:
            obj = json.loads(raw)
        except ValueError:
            continue
        if isinstance(obj, dict):
            parse(item, obj)
    item.offset += end + 1
    item.size, item.mtime = stat.st_size, stat.st_mtime


def _claude(target: Path, run: Path, since: float) -> Iterable[Transcript]:
    for path in _claude_home().glob("projects/*/*/subagents/*.jsonl"):
        try:
            if path.stat().st_mtime < since:
                continue
        except OSError:
            continue
        key = f"claude:{run}:{path}"
        item = _CACHE.get(key)
        if item is None:
            item = _CACHE[key] = Transcript("claude", path)
            try:
                meta = json.loads(path.with_suffix(".meta.json").read_text(encoding="utf-8"))
                item.stage = stage_of(meta.get("agentType")) or stage_of(meta.get("description"))
            except (OSError, ValueError):
                pass
        if item.bound is False:
            continue
        _read(item, lambda entry, obj: _claude_line(entry, obj, target, run))
        if item.bound:
            yield item


def _codex(target: Path, since: float) -> Iterable[Transcript]:
    root = _codex_home() / "sessions"
    # Sessions are filed by local date: only the days the run could have used.
    day = 86_400
    first = datetime.fromtimestamp(since - day).date()
    last = datetime.fromtimestamp(max(since, datetime.now().timestamp()) + day).date()
    days = []
    current = first
    while current <= last and len(days) < 60:
        days.append(root / f"{current.year:04d}" / f"{current.month:02d}" / f"{current.day:02d}")
        current = datetime.fromordinal(current.toordinal() + 1).date()
    for folder in days:
        for path in folder.glob("rollout-*.jsonl"):
            try:
                if path.stat().st_mtime < since:
                    continue
            except OSError:
                continue
            key = f"codex:{target}:{path}"
            item = _CACHE.get(key)
            if item is None:
                item = _CACHE[key] = Transcript("codex", path)
            if item.bound is False:
                continue
            _read(item, lambda entry, obj: _codex_line(entry, obj, target))
            if item.bound:
                yield item


def transcripts(target: Path, run: Path, since: float) -> list[Transcript]:
    """Every host transcript of an agent of this run that was written to after `since`."""
    try:
        target = target.resolve()
    except OSError:
        pass
    found: list[Transcript] = []
    for source in (lambda: _claude(target, run, since), lambda: _codex(target, since)):
        try:
            found.extend(item for item in source() if item.stage)
        except OSError:
            continue
    return found


def _matches(item: Transcript, stage: str, slice_id: str | None) -> bool:
    if item.stage != stage:
        return False
    if slice_id is None:
        return True
    # The usual Implementer desk is slice S1: an Implementer that names no slice is that one.
    return item.slice_id == slice_id or (slice_id == "S1" and item.slice_id is None)


def agent_tokens(found: list[Transcript], stage: str, slice_id: str | None, since: float) -> tuple[int, str] | None:
    """The tokens of the latest request of this stage's agent made after `since`, and the host."""
    best: tuple[float, int, str] | None = None
    for item in found:
        if not _matches(item, stage, slice_id):
            continue
        for when, tokens in reversed(item.records):
            if when < since:
                break
            if best is None or when > best[0]:
                best = (when, tokens, item.host)
            break
    return (best[1], best[2]) if best else None


def measure(target: Path, run: Path, stage: str, slice_id: str | None, since: float) -> tuple[int, str] | None:
    """One stage's agent, for `log`: what it used in the round that began at `since`."""
    if os.environ.get("ANDROID_WORKFLOW_NO_USAGE"):
        return None
    return agent_tokens(transcripts(target, run, since), stage, slice_id, since)
