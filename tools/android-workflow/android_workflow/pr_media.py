"""Fill the PR template's screenshot section from this run's captures, by code.

Images are never uploaded anywhere: each Before/After cell names the file under `RUN/media/`, and
the developer drags it into the PR. The section is rebuilt on every `deliver`, so it always matches
the captures on disk. Rows a person already filled (an image URL or tag) are kept as they are.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")
VIDEO_SUFFIXES = (".mp4", ".webm")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
MEDIA_TITLE = re.compile(r"(?i)screen\s*shot|screenshot|screen\s*recording|visual|video|evidence|before.*after")
SEPARATOR = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$")
FILLED = re.compile(r"(?i)https?://|<img|<video|!\[")
NONE_LINE = "_No screenshots:"
NO_OPEN_ENV = "ANDROID_WORKFLOW_NO_OPEN"


def captures(agent_dir: Path) -> list[dict[str, Any]]:
    """One row per capture name and kind: `{name, kind, before, after}` with paths relative to `media/`."""
    media = agent_dir / "media"
    found: dict[tuple[str, str], dict[str, Any]] = {}
    for phase in ("before", "after"):
        folder = media / phase
        if not folder.is_dir():
            continue
        for path in sorted(folder.iterdir()):
            suffix = path.suffix.lower()
            if not path.is_file() or suffix not in IMAGE_SUFFIXES + VIDEO_SUFFIXES:
                continue
            kind = "video" if suffix in VIDEO_SUFFIXES else "image"
            row = found.setdefault((path.stem, kind), {"name": path.stem, "kind": kind, "before": None, "after": None})
            row[phase] = f"{phase}/{path.name}"
    return [found[key] for key in sorted(found, key=lambda key: (key[0], key[1] != "image"))]


def _cells(line: str) -> list[str]:
    text = line.strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|"):
        text = text[:-1]
    return [cell.strip() for cell in text.split("|")]


def _join(cells: list[str], piped: bool) -> str:
    inner = " | ".join(cells)
    return f"| {inner} |" if piped else inner


def _cell(path: str | None, kind: str) -> str:
    if not path:
        return "—"
    return f"`{path}`" + (" (video)" if kind == "video" else "")


def _section(lines: list[str]) -> tuple[int, int] | None:
    """`(heading index, end index)` of the first media heading, ending at the next heading of its level or above."""
    for index, line in enumerate(lines):
        match = HEADING.match(line)
        if match and MEDIA_TITLE.search(match.group(2)):
            level = len(match.group(1))
            for end in range(index + 1, len(lines)):
                other = HEADING.match(lines[end])
                if other and len(other.group(1)) <= level:
                    return index, end
            return index, len(lines)
    return None


def _table(lines: list[str], start: int, end: int) -> tuple[int, int, list[str]] | None:
    """`(header index, end index, header cells)` of the first Before/After table in `lines[start:end]`."""
    for index in range(start, end - 1):
        if "|" not in lines[index] or not SEPARATOR.match(lines[index + 1]):
            continue
        header = _cells(lines[index])
        lowered = [cell.lower() for cell in header]
        if any("before" in cell for cell in lowered) and any("after" in cell for cell in lowered):
            stop = index + 2
            while stop < end and "|" in lines[stop] and lines[stop].strip():
                stop += 1
            return index, stop, header
    return None


def _rows(header: list[str], rows: list[dict[str, Any]], piped: bool) -> list[str]:
    lowered = [cell.lower() for cell in header]
    before = next(i for i, cell in enumerate(lowered) if "before" in cell)
    after = next(i for i, cell in enumerate(lowered) if "after" in cell)
    label = next((i for i in range(len(header)) if i not in (before, after)), None)
    built = []
    for row in rows:
        cells = [""] * len(header)
        if label is not None:
            cells[label] = row["name"]
        cells[before] = _cell(row["before"], row["kind"])
        cells[after] = _cell(row["after"], row["kind"])
        built.append(_join(cells, piped))
    return built


def fill(body: str, rows: list[dict[str, Any]], ui: bool) -> str:
    """The body with its screenshot section rebuilt from `rows`; anything outside that section is untouched."""
    lines = body.rstrip("\n").split("\n")
    section = _section(lines)
    if section is None:
        if not rows:
            return body
        table = ["Screen | Before | After", "---|---|---", *_rows(["Screen", "Before", "After"], rows, False)]
        return body.rstrip("\n") + "\n\n### Screenshots\n\n" + "\n".join(table) + "\n"
    start, end = section
    kept = [line for line in lines[start + 1:end] if not line.strip().startswith(NONE_LINE)]
    lines = lines[:start + 1] + kept + lines[end:]
    end = start + 1 + len(kept)
    table = _table(lines, start + 1, end)
    if not rows:
        reason = "no device capture in this run" if ui else "not a UI change"
        replacement = [f"{NONE_LINE} {reason}._"]
        if table:
            lines[table[0]:table[1]] = replacement
        else:
            lines[start + 1:start + 1] = ["", *replacement]
        return "\n".join(lines) + "\n"
    if table:
        header_index, stop, header = table
        filled = [line for line in lines[header_index + 2:stop] if FILLED.search(line)]
        piped = lines[header_index].strip().startswith("|")
        missing = [row for row in rows if not any(row["name"] in _cells(line) for line in filled)]
        lines[header_index + 2:stop] = filled + _rows(header, missing, piped)
    else:
        built = ["Screen | Before | After", "---|---|---", *_rows(["Screen", "Before", "After"], rows, False)]
        lines[start + 1:start + 1] = ["", *built]
    return "\n".join(lines) + "\n"


def files(agent_dir: Path, rows: list[dict[str, Any]]) -> list[str]:
    """Absolute paths in table order (before, then after, row by row): the order to drag them into the PR."""
    media = agent_dir / "media"
    return [str(media / row[phase]) for row in rows for phase in ("before", "after") if row[phase]]


def unpaired(rows: list[dict[str, Any]]) -> list[str]:
    return [f"{row['name']} ({'before' if row['before'] else 'after'} only)"
            for row in rows if bool(row["before"]) != bool(row["after"])]


def apply(agent_dir: Path, body: str, spec: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    """`(body with the screenshot section filled, what deliver reports)`; nothing to report without captures."""
    rows = captures(agent_dir)
    text = fill(body, rows, "ui" in (spec.get("surfaces") or []))
    if not rows:
        return text, None
    return text, {"rows": len(rows), "folder": str(agent_dir / "media"), "files": files(agent_dir, rows),
                  "unpaired": unpaired(rows)}


def reveal(folder: str) -> bool:
    """Open the media folder in Finder so the files are one drag away; macOS only, never fails delivery."""
    if os.environ.get(NO_OPEN_ENV) or sys.platform != "darwin" or not shutil.which("open"):
        return False
    try:
        return subprocess.run(["open", folder], capture_output=True, timeout=10, check=False).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False
