"""Maestro flows: the cheap way for the Device agent to start one, and the ones already written.

`scaffold` turns the Planner's navigation recipe into flow files under `RUN/media/flows/`: `_nav.yaml`
(launch and the recipe as numbered TODOs) and one flow per evidence name the recipe ends on, each
already capturing under that name. The agent fills in the selectors and runs `CLI device replay`;
that is less work than driving by hand with coordinates, and the next round replays it for free.

`known` lists flows earlier tickets of this repository proved (their runs in this checkout and in
the main checkout, and the per-repository library a passing replay feeds), so navigation to a
screen another ticket already reached is copied, not rediscovered.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

from android_workflow.paths import media_dir, run_dir, state_home, workflow_root

EVIDENCE_LINE = re.compile(r"(?i)\b(capture|evidence|screenshot|clip|record)\b")
EVIDENCE_NAME = re.compile(r"`([a-z0-9]+(?:-[a-z0-9]+)+)`")
RECIPE_HEADING = re.compile(r"(?i)navigation recipe")
PERF_TOPICS = frozenset({"performance", "compose-performance"})
KNOWN_LIMIT = 30


def recipe(plan: str) -> list[str]:
    """The navigation recipe's lines from `plan.md`: from its heading to the next heading."""
    lines = plan.splitlines()
    start = next((index for index, line in enumerate(lines) if RECIPE_HEADING.search(line)), None)
    if start is None:
        return []
    found = [lines[start]]
    stepped = False
    for line in lines[start + 1:]:
        step = bool(re.match(r"^\s*(\d+\.|-|\*)\s", line))
        stepped = stepped or bool(re.match(r"^\s*\d+\.\s", line))
        new_section = bool(line) and not line[0].isspace() and not step and stepped
        if line.startswith("#") or new_section:
            break  # the next heading, or a new paragraph once the numbered steps began
        found.append(line)
    while found and not found[-1].strip():
        found.pop()
    return found


def evidence_names(lines: list[str]) -> list[str]:
    names: list[str] = []
    for line in lines:
        if EVIDENCE_LINE.search(line):
            for name in EVIDENCE_NAME.findall(line):
                if name not in names and not name.endswith("gfxinfo"):  # a measurement, not a screen
                    names.append(name)
    return names


def _perf_ticket(agent_dir: Path) -> bool:
    import json

    try:
        picked = json.loads((agent_dir / "skills.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    for items in picked.values():
        if isinstance(items, list) and any(set(item.get("topics") or []) & PERF_TOPICS
                                           for item in items if isinstance(item, dict)):
            return True
    return False


NAV = """appId: {app_id}
---
# Shared navigation to the changed screen. Every flow starts with `- runFlow: _nav.yaml`.
# Fill each TODO with a selector (text, id or test tag, never coordinates) and wait on what the
# step shows, never a sleep. A deep link beats tapping through screens: put it in a `.sh`.
- launchApp
{steps}"""

FLOW = """appId: {app_id}
---
- runFlow: _nav.yaml
# TODO: the steps that reach the state `{name}` shows, then wait on something visible in it:
# - tapOn: "..."
# - extendedWaitUntil:
#     visible:
#       id: "..."
#     timeout: 15000
- takeScreenshot: {name}
"""

ERROR_SCRIPT = """#!/bin/sh
# Runs instead of {name}.yaml: forces the error, runs the flow, restores the network (the replay
# turns wifi and data back on after any script too). Split the yaml at the point where the network
# must go off if the error needs it later than launch.
adb -s "$AW_SERIAL" shell svc wifi disable
adb -s "$AW_SERIAL" shell svc data disable
"$AW_MAESTRO" --device "$AW_SERIAL" test --test-output-dir "$AW_OUT" "$AW_FLOW"
code=$?
adb -s "$AW_SERIAL" shell svc wifi enable
adb -s "$AW_SERIAL" shell svc data enable
exit $code
"""

PERF_SCRIPT = """#!/bin/sh
# Frame timing for a performance ticket: resets gfxinfo, runs the flow, saves the frame stats under
# the flow's name; the replay files `<flow>-gfxinfo.txt` next to the captures, in before and after.
# An emulator renders in software: its numbers compare before with after on the same emulator only;
# say so in the report, and prefer a physical device when one is connected.
adb -s "$AW_SERIAL" shell dumpsys gfxinfo {app_id} reset >/dev/null
"$AW_MAESTRO" --device "$AW_SERIAL" test --test-output-dir "$AW_OUT" "$AW_FLOW"
code=$?
adb -s "$AW_SERIAL" shell dumpsys gfxinfo {app_id} > "$AW_OUT/{name}-gfxinfo.txt"
exit $code
"""


def scaffold(target: Path, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Write flow templates from the plan's navigation recipe; never overwrite a flow that exists."""
    agent_dir = run_dir(target)
    folder = media_dir(target) / "flows"
    folder.mkdir(parents=True, exist_ok=True)
    app_id = ((config or {}).get("device") or {}).get("application_id") or "TODO.application.id"
    try:
        plan = (agent_dir / "plan.md").read_text(encoding="utf-8")
    except OSError:
        plan = ""
    lines = recipe(plan)
    names = evidence_names(lines) or [f"{agent_dir.name.lower()}-after"]
    written: list[str] = []
    kept: list[str] = []

    def put(name: str, text: str, executable: bool = False) -> None:
        path = folder / name
        if path.exists():
            kept.append(name)
            return
        path.write_text(text, encoding="utf-8")
        if executable:
            path.chmod(0o755)
        written.append(name)

    steps = [line.strip() for line in lines[1:] if line.strip()]
    put("_nav.yaml", NAV.format(app_id=app_id, steps="".join(f"# TODO {step}\n" for step in steps)
                                or "# TODO: no navigation recipe in plan.md; derive it from the NavHost/intent code\n"))
    perf = _perf_ticket(agent_dir)
    for name in names:
        put(f"{name}.yaml", FLOW.format(app_id=app_id, name=name))
        if re.search(r"(error|offline|retry|network)", name):
            put(f"{name}.sh", ERROR_SCRIPT.format(name=name), executable=True)
        elif perf and not (folder / f"{name}.sh").exists():
            put(f"{name}.sh", PERF_SCRIPT.format(app_id=app_id, name=name), executable=True)
    return {
        "status": "scaffolded" if written else "kept",
        "folder": str(folder),
        "written": written,
        "kept": kept,
        "evidence_names": names,
        "known_flows": known(target)[:KNOWN_LIMIT],
        "next": "fill the TODO selectors (reuse a known flow where it reaches the same screen), then "
                "`CLI device replay --target TARGET --serial SERIAL`; fix a failing flow, never the app",
    }


def library(target: Path) -> Path | None:
    from android_workflow.base import home, repository

    repo = repository(target)
    return home(repo["id"]) / "flows" if repo else None


def remember(target: Path) -> str | None:
    """After a replay that passed: keep this run's flows in the repository's library."""
    root = library(target)
    source = media_dir(target) / "flows"
    if root is None or not source.is_dir():
        return None
    destination = root / run_dir(target).name
    shutil.rmtree(destination, ignore_errors=True)
    destination.mkdir(parents=True, exist_ok=True)
    for path in source.iterdir():
        if path.is_file() and path.suffix in {".yaml", ".yml", ".sh"}:
            shutil.copy2(path, destination / path.name)
    return str(destination)


def known(target: Path) -> list[dict[str, Any]]:
    """Flows other tickets of this repository proved: newest first, one line of what each does."""
    from android_workflow.base import repository

    folders: list[Path] = []
    current = run_dir(target).resolve()
    roots = [workflow_root(target)]
    repo = repository(target)
    if repo and Path(repo["main"]).resolve() != target.resolve():
        roots.append(workflow_root(Path(repo["main"])))
    for root in roots:
        if root.is_dir():
            folders.extend(path / "media" / "flows" for path in root.iterdir()
                           if path.is_dir() and path.resolve() != current)
    lib = library(target)
    if lib and lib.is_dir():
        folders.extend(path for path in lib.iterdir() if path.is_dir() and path.name != current.name)
    seen: set[tuple[str, str]] = set()
    found: list[dict[str, Any]] = []
    for folder in folders:
        if not folder.is_dir():
            continue
        ticket = folder.parent.parent.name if folder.name == "flows" else folder.name
        for path in folder.glob("*.yaml"):
            key = (ticket, path.name)
            if key in seen:
                continue
            seen.add(key)
            try:
                text = path.read_text(encoding="utf-8")
                mtime = path.stat().st_mtime
            except OSError:
                continue
            steps = [line.strip("- ").strip() for line in text.split("---", 1)[-1].splitlines()
                     if line.strip().startswith("-")][:4]
            found.append({"ticket": ticket, "path": str(path), "steps": "; ".join(steps), "mtime": mtime})
    found.sort(key=lambda item: item["mtime"], reverse=True)
    return [{key: value for key, value in item.items() if key != "mtime"} for item in found]
