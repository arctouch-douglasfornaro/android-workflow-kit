"""Build the base APK in the background while the Planner and the Implementer work.

The build runs in the repository's base checkout (`base.py`), never in the ticket's own checkout, so
it cannot race the Implementer's Gradle runs or pick up its edits, and it may start at any point of
the run. Its APKs are cached by base commit: a second run on the same base gets them at once.
`RUN/prebuild.json` records the outcome for the Device `before` and the late `before` replay.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from android_workflow.base import DEFAULT_BASE_TIMEOUT
from android_workflow.paths import cache_dir, run_dir

TOOLKIT_ROOT = Path(__file__).resolve().parent.parent


def prebuild_path(target: Path) -> Path:
    return run_dir(target) / "prebuild.json"


def read_record(target: Path) -> dict[str, Any]:
    path = prebuild_path(target)
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except json.JSONDecodeError:
        return {}


def write_record(target: Path, record: dict[str, Any]) -> None:
    path = prebuild_path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def current_status(target: Path) -> dict[str, Any]:
    record = read_record(target)
    starting = record.get("pid") is None and time.time() - float(record.get("started_at") or 0) < 30
    if record.get("status") == "running" and not starting and not pid_alive(record.get("pid")):
        record = {**record, "status": "failed", "reason": "build process ended without a result"}
        write_record(target, record)
    return record or {"status": "not_started"}


def start_prebuild(target: Path, force: bool = False) -> dict[str, Any]:
    from android_workflow.base import base_commit, cached, repository
    from android_workflow.cli import read_json

    existing = current_status(target)
    if existing.get("status") in {"running", "passed"}:
        return existing
    spec_path = run_dir(target) / "ticket-spec.json"
    spec = read_json(spec_path) if spec_path.is_file() else {}
    if not force and "T7" not in (spec.get("route") or []):
        return {"status": "skipped", "reason": "no device stage in this run's route"}
    config_path = cache_dir(target) / "project-config.json"
    config = read_json(config_path) if config_path.is_file() else {}
    command = (config.get("commands") or {}).get("build")
    if not command:
        return {"status": "skipped", "reason": "no build command recorded by Setup"}
    repo, sha = repository(target), base_commit(target)
    if not repo or not sha:
        return {"status": "skipped", "reason": "no git base commit to build"}
    log = run_dir(target) / "prebuild.log"
    now = time.time()
    hit = cached(repo["id"], sha)
    if hit:
        record = {"status": "passed", "command": command, "sha": sha, "repo": repo["id"], "cached": True,
                  "started_at": now, "finished_at": now, "duration_seconds": 0.0, "apks": hit}
        write_record(target, record)
        return record
    execution = config.get("execution") or {}
    record = {
        "status": "running",
        "command": command,
        "sha": sha,
        "repo": repo["id"],
        "log": str(log),
        "started_at": now,
        "pid": None,
        "timeout_seconds": execution.get("base_build_timeout_seconds") or DEFAULT_BASE_TIMEOUT,
    }
    write_record(target, record)
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]); "
        "from android_workflow.prebuild import worker; worker(sys.argv[2])"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(TOOLKIT_ROOT), str(target)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    record["pid"] = process.pid
    write_record(target, record)
    return record


def worker(target_text: str) -> None:
    from android_workflow.base import build
    from android_workflow.cli import read_json

    target = Path(target_text)
    record = read_record(target)
    started = record.get("started_at") or time.time()
    config_path = cache_dir(target) / "project-config.json"
    config = read_json(config_path) if config_path.is_file() else {}

    def waiting(why: str) -> None:
        write_record(target, {**read_record(target), "waiting": why})

    try:
        result = build(target, record["command"], config=config, log=Path(record["log"]),
                       timeout=float(record.get("timeout_seconds") or DEFAULT_BASE_TIMEOUT), on_wait=waiting)
    except Exception as error:  # noqa: BLE001 - the record must never stay "running"
        result = {"status": "failed", "reason": f"the base build crashed: {error}"}
    final = {key: value for key, value in read_record(target).items() if key != "waiting"}
    write_record(target, {
        **final,
        **result,
        "duration_seconds": round(time.time() - started, 1),
        "finished_at": time.time(),
        "apks": result.get("apks") or [],
    })


def wait_prebuild(target: Path, timeout: float = float(DEFAULT_BASE_TIMEOUT), poll: float = 2.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        record = current_status(target)
        if record.get("status") != "running":
            return record
        if time.monotonic() >= deadline:
            return {**record, "waiting": "timed_out"}
        time.sleep(poll)
