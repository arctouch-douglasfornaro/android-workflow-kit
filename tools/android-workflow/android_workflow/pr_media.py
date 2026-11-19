"""Fill the PR template's screenshot section from this run's captures, by code.

With uploads on (GitHub and a `gh` that has `--attach`), each Before/After cell references the file
under `RUN/media/` (`![name before](media/before/x.png)`; a video is a link) and `gh --attach` uploads
it and swaps the path for the asset URL. `RUN/media-uploads.json` remembers each upload by content
hash, so the next `deliver` reuses the URL and uploads only a capture that changed. Without uploads
each cell names the file and the developer drags it into the PR. The section is rebuilt on every
`deliver`, so it always matches the captures on disk. Rows a person filled (an image URL or tag the
workflow did not upload) are kept as they are.
"""

from __future__ import annotations

import hashlib
import json
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
LEDGER = "media-uploads.json"  # {"before/a.png": {"sha256", "url"}}: what `gh --attach` uploaded
# A markdown image or link with its target: our local `media/...` path, or a URL we uploaded.
REF = re.compile(r"!?\[((?:\\.|[^\]\\])*)\]\(([^)\s]+)\)")


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


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ledger(agent_dir: Path) -> dict[str, dict[str, str]]:
    try:
        data = json.loads((agent_dir / LEDGER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {key: value for key, value in data.items() if isinstance(value, dict) and value.get("url")} \
        if isinstance(data, dict) else {}


def _alt(name: str, phase: str, kind: str) -> str:
    text = re.sub(r"[\[\]|\\]", "", f"{name} {phase}")
    return f"{text} (video)" if kind == "video" else text


def _cell(row: dict[str, Any], phase: str, attach: dict[str, Any] | None = None) -> str:
    """A Before/After cell: the file name to drag (no uploads), the uploaded asset when the capture is
    unchanged since it was uploaded, or a `media/...` reference for `gh --attach` to upload."""
    path, kind = row[phase], row["kind"]
    if not path:
        return "—"
    if attach is None:
        return f"`{path}`" + (" (video)" if kind == "video" else "")
    alt = _alt(row["name"], phase, kind)
    known = attach["ledger"].get(path)
    target = known["url"] if known and known.get("sha256") == attach["hashes"].get(path) else f"media/{path}"
    return f"[{alt}]({target})" if kind == "video" else f"![{alt}]({target})"


def _ours(line: str, urls: set[str]) -> str:
    """`line` without the references the workflow wrote (local `media/` paths and URLs it uploaded)."""
    return REF.sub(lambda m: "" if m.group(2).startswith("media/") or m.group(2) in urls else m.group(0), line)


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


def _rows(header: list[str], rows: list[dict[str, Any]], piped: bool,
          attach: dict[str, Any] | None = None) -> list[str]:
    lowered = [cell.lower() for cell in header]
    before = next(i for i, cell in enumerate(lowered) if "before" in cell)
    after = next(i for i, cell in enumerate(lowered) if "after" in cell)
    label = next((i for i in range(len(header)) if i not in (before, after)), None)
    built = []
    for row in rows:
        cells = [""] * len(header)
        if label is not None:
            cells[label] = row["name"]
        cells[before] = _cell(row, "before", attach)
        cells[after] = _cell(row, "after", attach)
        built.append(_join(cells, piped))
    return built


def fill(body: str, rows: list[dict[str, Any]], ui: bool, attach: dict[str, Any] | None = None) -> str:
    """The body with its screenshot section rebuilt from `rows`; anything outside that section is untouched.
    `attach` (`{ledger, hashes}`) writes references for `gh --attach` instead of file names to drag."""
    lines = body.rstrip("\n").split("\n")
    section = _section(lines)
    if section is None:
        if not rows:
            return body
        table = ["Screen | Before | After", "---|---|---", *_rows(["Screen", "Before", "After"], rows, False, attach)]
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
        # A row is a person's when it still holds an image after the workflow's own references are taken out.
        urls = {entry["url"] for entry in (attach or {}).get("ledger", {}).values()}
        filled = [line for line in lines[header_index + 2:stop] if FILLED.search(_ours(line, urls))]
        piped = lines[header_index].strip().startswith("|")
        missing = [row for row in rows if not any(row["name"] in _cells(line) for line in filled)]
        lines[header_index + 2:stop] = filled + _rows(header, missing, piped, attach)
    else:
        built = ["Screen | Before | After", "---|---|---", *_rows(["Screen", "Before", "After"], rows, False, attach)]
        lines[start + 1:start + 1] = ["", *built]
    return "\n".join(lines) + "\n"


def files(agent_dir: Path, rows: list[dict[str, Any]]) -> list[str]:
    """Absolute paths in table order (before, then after, row by row): the order to drag them into the PR."""
    media = agent_dir / "media"
    return [str(media / row[phase]) for row in rows for phase in ("before", "after") if row[phase]]


def unpaired(rows: list[dict[str, Any]]) -> list[str]:
    # A JVM render has no before by design (`render-*`): it is never reported as missing its pair.
    return [f"{row['name']} ({'before' if row['before'] else 'after'} only)"
            for row in rows if bool(row["before"]) != bool(row["after"]) and not row["name"].startswith("render-")]


def apply(agent_dir: Path, body: str, spec: dict[str, Any],
          upload: bool = False) -> tuple[str, dict[str, Any] | None]:
    """`(body with the screenshot section filled, what deliver reports)`; nothing to report without captures.
    `upload`: reference the captures for `gh --attach` (`pending` lists what still has to go up)."""
    rows = captures(agent_dir)
    attach = None
    if upload:
        media = agent_dir / "media"
        hashes = {row[phase]: _sha(media / row[phase]) for row in rows for phase in ("before", "after") if row[phase]}
        attach = {"ledger": ledger(agent_dir), "hashes": hashes}
    text = fill(body, rows, "ui" in (spec.get("surfaces") or []), attach)
    if not rows:
        return text, None
    return text, {"rows": len(rows), "folder": str(agent_dir / "media"), "files": files(agent_dir, rows),
                  "unpaired": unpaired(rows), "upload": upload}


def pending(agent_dir: Path, body: str) -> list[str]:
    """Absolute paths of the captures `body` still references locally (`media/...`): what `gh --attach`
    uploads. Only a referenced file is attached, since `gh` appends an unreferenced one to the body."""
    media = (agent_dir / "media").resolve()
    found: list[str] = []
    for match in REF.finditer(body):
        target = match.group(2)
        if not target.startswith("media/"):
            continue
        path = (agent_dir / target).resolve()
        if path.is_file() and media in path.parents and str(path) not in found:
            found.append(str(path))
    return found


def record(agent_dir: Path, body: str) -> dict[str, str]:
    """Remember what `gh --attach` uploaded: each workflow reference in `body` (the PR as it is now) whose
    alt text names a capture and whose target is an asset URL. Returns `{capture: url}` added."""
    by_alt = {}
    for row in captures(agent_dir):
        for phase in ("before", "after"):
            if row[phase]:
                by_alt[_alt(row["name"], phase, row["kind"])] = row[phase]
    known = ledger(agent_dir)
    added: dict[str, str] = {}
    for match in REF.finditer(body):
        alt, url = match.group(1), match.group(2)
        path = by_alt.get(alt)
        if not path or not url.startswith("https://"):
            continue
        sha = _sha(agent_dir / "media" / path)
        if known.get(path) != {"sha256": sha, "url": url}:
            known[path] = {"sha256": sha, "url": url}
            added[path] = url
    if added:
        (agent_dir / LEDGER).write_text(json.dumps(known, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return added


def to_manual(body: str) -> str:
    """`body` with every local `media/...` reference turned back into the file name to drag (uploads off
    or failed): a path GitHub cannot resolve would render as a broken image."""
    def swap(match: re.Match[str]) -> str:
        target = match.group(2)
        if not target.startswith("media/"):
            return match.group(0)
        return f"`{target[len('media/'):]}`" + (" (video)" if match.group(1).endswith("(video)") else "")
    return REF.sub(swap, body)


def reveal(folder: str) -> bool:
    """Open the media folder in Finder so the files are one drag away; macOS only, never fails delivery."""
    if os.environ.get(NO_OPEN_ENV) or sys.platform != "darwin" or not shutil.which("open"):
        return False
    try:
        return subprocess.run(["open", folder], capture_output=True, timeout=10, check=False).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False
