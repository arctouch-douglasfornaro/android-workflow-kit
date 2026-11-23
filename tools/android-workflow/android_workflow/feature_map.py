"""The app's feature map: every surface a device run may need and how to reach it from launch.

One file per app, `.ai/feature-map.md` in the main checkout (worktrees and PR reviews share it). The
Setup agent writes it (`feature_map` step), the Planner and the Device read it, and the Device adds the
routes it learns with `CLI feature-map --learn`. Its status is computed, never stored: `missing`, `stale`
when an entry point in the code (a manifest activity, a navigation graph file) is neither mapped nor
listed under Not mapped, else `ok`.
"""

from __future__ import annotations

import contextlib
import fcntl
import fnmatch
import re
import subprocess
import xml.etree.ElementTree as ElementTree
from pathlib import Path
from typing import Any, Iterator

MAP_FILE = Path(".ai") / "feature-map.md"
LOCK_FILE = Path(".ai") / "workflow" / "_setup" / ".feature-map.lock"
COLUMNS = ("Surface", "Entry", "How to reach", "Deep link", "Selectors", "Prerequisites", "Source")
REQUIRED = ("Surface", "How to reach", "Source")
NOT_MAPPED_COLUMNS = ("Entry", "Why")
ANDROID_NAME = "{http://schemas.android.com/apk/res/android}name"
NAV_PATTERN = r"NavHost\(|fun NavGraphBuilder\.|NavGraphBuilder\.\w+\("
SKIPPED_SOURCE_SETS = re.compile(r"(^|/)src/(test|androidTest|testFixtures|debug|sharedTest)\w*/")
PRUNE = {".git", ".gradle", ".idea", "build", "out", "node_modules", ".kotlin", ".ai"}
SHOWN = 40
# Never in the map: it is local text agents read, and a run must not copy account data into it.
SECRET = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+|\b(password|passwd|token|secret|api[_-]?key)\s*[:=]", re.I)

TEMPLATE = """# Feature map

Every surface a device run may need and how to reach it from a cold launch. Read by the Planner
and the Device; written by Setup and `CLI feature-map --learn`. No credentials or user data:
name a test account by its role, never its login.

## Surfaces

| Surface | Entry | How to reach | Deep link | Selectors | Prerequisites | Source |
|---|---|---|---|---|---|---|

## Not mapped

Entry points left out on purpose (debug, test, internal). Globs work: `*Debug*`, `feature/internal/**`.

| Entry | Why |
|---|---|
"""


def map_path(home: Path) -> Path:
    return home / MAP_FILE


def _split_row(line: str) -> list[str]:
    cells = re.split(r"(?<!\\)\|", line.strip().strip("|"))
    return [cell.strip() for cell in cells]


def _section(text: str, heading: str) -> str:
    match = re.search(rf"^## {re.escape(heading)}\s*$(.*?)(?=^## |\Z)", text, re.M | re.S)
    return match.group(1) if match else ""


def _table(section: str, columns: tuple[str, ...]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    header: list[str] | None = None
    for line in section.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = _split_row(line)
        if header is None:
            header = cells
            continue
        if all(re.fullmatch(r":?-*:?", cell) for cell in cells):
            continue
        row = {name: (cells[index] if index < len(cells) else "") for index, name in enumerate(header)}
        if any(row.values()):
            rows.append({name: row.get(name, "") for name in columns})
    return rows


def parse(text: str) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """`(surfaces, not_mapped)` rows of a map's two tables."""
    return _table(_section(text, "Surfaces"), COLUMNS), _table(_section(text, "Not mapped"), NOT_MAPPED_COLUMNS)


def _row_line(row: dict[str, str], columns: tuple[str, ...]) -> str:
    return "| " + " | ".join(row.get(name, "").replace("\n", " ") for name in columns) + " |"


def _replace_table(text: str, heading: str, rows: list[dict[str, str]], columns: tuple[str, ...]) -> str:
    if not re.search(rf"^## {re.escape(heading)}\s*$", text, re.M):
        text = text.rstrip() + f"\n\n## {heading}\n"
    start = re.search(rf"^## {re.escape(heading)}\s*$\n?", text, re.M)
    assert start is not None
    after = text[start.end():]
    following = re.search(r"^## ", after, re.M)
    body, tail = (after[:following.start()], after[following.start():]) if following else (after, "")
    lines = body.splitlines()
    table_lines = [index for index, line in enumerate(lines) if line.strip().startswith("|")]
    table = ["| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)]
    table += [_row_line(row, columns) for row in rows]
    if table_lines:
        lines = lines[:table_lines[0]] + table + lines[table_lines[-1] + 1:]
    else:
        lines = lines + [""] + table
    return text[:start.end()] + "\n" + "\n".join(lines).strip("\n") + "\n\n" + tail


def render(surfaces: list[dict[str, str]], not_mapped: list[dict[str, str]], text: str | None = None) -> str:
    """The map with these rows; anything else a human wrote around the tables stays."""
    text = text if text and "## Surfaces" in text else TEMPLATE
    text = _replace_table(text, "Surfaces", surfaces, COLUMNS)
    text = _replace_table(text, "Not mapped", not_mapped, NOT_MAPPED_COLUMNS)
    return re.sub(r"\n{3,}", "\n\n", text).rstrip() + "\n"


def _git(target: Path, *args: str) -> str | None:
    result = subprocess.run(["git", "-C", str(target), *args], text=True, capture_output=True, check=False)
    return result.stdout if result.returncode == 0 else None


def _source_files(target: Path, suffixes: tuple[str, ...]) -> list[str]:
    listed = _git(target, "ls-files")
    if listed is not None:
        names = listed.splitlines()
    else:  # not a git checkout (a test fixture): walk it
        names = []
        for path in target.rglob("*"):
            if path.is_file() and not (set(path.relative_to(target).parts) & PRUNE):
                names.append(path.relative_to(target).as_posix())
    return sorted(name for name in names if name.endswith(suffixes) and not SKIPPED_SOURCE_SETS.search(name))


def entry_points(target: Path) -> list[dict[str, str]]:
    """What a user can land on: every activity a release manifest declares and every file that builds a
    navigation graph (`NavHost(`, `NavGraphBuilder` extensions, `res/navigation/*.xml`)."""
    found: dict[str, dict[str, str]] = {}
    files = _source_files(target, ("AndroidManifest.xml", ".xml", ".kt"))
    for name in files:
        if name.endswith("AndroidManifest.xml"):
            try:
                root = ElementTree.parse(target / name).getroot()
            except (ElementTree.ParseError, OSError):
                continue
            for activity in root.iter("activity"):
                declared = activity.get(ANDROID_NAME) or ""
                key = declared.rsplit(".", 1)[-1]
                if key and key not in found:
                    found[key] = {"key": key, "kind": "activity", "path": name}
        elif "/res/navigation/" in name and name.endswith(".xml"):
            found.setdefault(name, {"key": name, "kind": "navigation", "path": name})
    kotlin = [name for name in files if name.endswith(".kt")]
    grep = _git(target, "grep", "-l", "-E", NAV_PATTERN, "--", "*.kt")
    if grep is not None:
        known = set(kotlin)
        graphs = [name for name in grep.splitlines() if name in known]
    else:
        pattern = re.compile(NAV_PATTERN)
        graphs = [name for name in kotlin if pattern.search((target / name).read_text(errors="replace"))]
    for name in graphs:
        found.setdefault(name, {"key": name, "kind": "navigation", "path": name})
    return sorted(found.values(), key=lambda item: (item["kind"], item["key"]))


def _covered(entry: dict[str, str], surfaces: list[dict[str, str]], not_mapped: list[dict[str, str]]) -> bool:
    key = entry["key"]
    word = re.compile(rf"(?<![\w.]){re.escape(key)}(?![\w])") if entry["kind"] == "activity" else None
    for row in surfaces:
        haystack = f"{row.get('Entry', '')} {row.get('Source', '')}"
        if (word.search(haystack) if word else key in haystack):
            return True
    for row in not_mapped:
        pattern = row.get("Entry", "").strip("` ")
        if pattern and (pattern == key or fnmatch.fnmatch(key, pattern) or fnmatch.fnmatch(entry["path"], pattern)):
            return True
    return False


SOURCE_PATH = re.compile(r"[\w./-]+\.(?:kt|java|xml)")


def status(target: Path, home: Path) -> dict[str, Any]:
    """Where the map is and whether a run can rely on it. `uncovered` lists entry points in TARGET's code
    the map neither describes nor skips; `orphans` are rows whose source file TARGET lacks (another branch,
    or a removed screen): reported, never a reason to rebuild."""
    path = map_path(home)
    entries = entry_points(target)
    result: dict[str, Any] = {"path": str(path), "entry_points": len(entries)}
    if not path.is_file():
        return {**result, "status": "missing", "rows": 0, "uncovered_count": len(entries),
                "uncovered": [entry["key"] for entry in entries[:SHOWN]]}
    surfaces, not_mapped = parse(path.read_text(errors="replace"))
    incomplete = [row["Surface"] or "(unnamed)" for row in surfaces if not all(row.get(name) for name in REQUIRED)]
    uncovered = [entry["key"] for entry in entries if not _covered(entry, surfaces, not_mapped)]
    orphans = sorted({row["Surface"] for row in surfaces for source in SOURCE_PATH.findall(row.get("Source", ""))
                      if "/" in source and not (target / source).is_file()})
    if not surfaces:
        state = "missing"
    elif uncovered or incomplete:
        state = "stale"
    else:
        state = "ok"
    return {**result, "status": state, "rows": len(surfaces), "not_mapped": len(not_mapped),
            "uncovered_count": len(uncovered), "uncovered": uncovered[:SHOWN],
            "incomplete_rows": incomplete, "orphans": orphans}


@contextlib.contextmanager
def _locked(home: Path) -> Iterator[None]:
    lock = home / LOCK_FILE
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def init(home: Path) -> Path:
    """The map's skeleton, only when there is none."""
    path = map_path(home)
    with _locked(home):
        if not path.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(TEMPLATE, encoding="utf-8")
    return path


def learn(home: Path, rows_file: Path) -> dict[str, Any]:
    """Merge the rows of ROWS_FILE's tables into the map: a row replaces the one with the same Surface
    (or Not mapped Entry), else it is added. Several runs learn at once, so the merge holds a lock."""
    surfaces_in, skipped_in = parse(rows_file.read_text(errors="replace"))
    if not surfaces_in and not skipped_in:
        raise ValueError(f"{rows_file} has no `## Surfaces` or `## Not mapped` table rows")
    for row in surfaces_in:
        missing = [name for name in REQUIRED if not row.get(name)]
        if missing:
            raise ValueError(f"row `{row.get('Surface') or '(unnamed)'}` lacks {', '.join(missing)}")
    for row in surfaces_in + skipped_in:
        if any(SECRET.search(value) for value in row.values()):
            raise ValueError(f"row `{row.get('Surface') or row.get('Entry')}` looks like it holds an account "
                             "login or a secret; name the test account by its role instead")
    path = map_path(home)
    with _locked(home):
        text = path.read_text(errors="replace") if path.is_file() else TEMPLATE
        surfaces, not_mapped = parse(text)
        added, replaced = [], []
        for incoming, rows, key in ((surfaces_in, surfaces, "Surface"), (skipped_in, not_mapped, "Entry")):
            for row in incoming:
                index = next((i for i, old in enumerate(rows) if old[key].lower() == row[key].lower()), None)
                if index is None:
                    rows.append(row)
                    added.append(row[key])
                else:
                    rows[index] = row
                    replaced.append(row[key])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render(surfaces, not_mapped, text), encoding="utf-8")
    return {"path": str(path), "added": added, "replaced": replaced}
