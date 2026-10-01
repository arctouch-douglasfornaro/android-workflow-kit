"""Build the base APK in the background while the Planner reads code.

The base build only needs the unmodified source, and the Planner never edits it, so the two can
overlap. The build is discovered from the project's recorded `build` command, and it is refused
once the source has changed: an APK built from a half-edited tree would not be the "before" state.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

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


def find_apks(target: Path, since: float) -> list[dict[str, Any]]:
    """Debuggable-looking APKs written by the build, newest first."""
    found = []
    for path in target.glob("**/build/outputs/apk/**/*.apk"):
        try:
            stat = path.stat()
        except OSError:
            continue
        if stat.st_mtime >= since - 1 and "androidTest" not in path.parts:
            found.append({"path": str(path), "bytes": stat.st_size, "mtime": stat.st_mtime})
    return sorted(found, key=lambda item: item["mtime"], reverse=True)


def current_status(target: Path) -> dict[str, Any]:
    record = read_record(target)
    if record.get("status") == "running" and not pid_alive(record.get("pid")):
        record = {**record, "status": "failed", "reason": "build process ended without a result"}
        write_record(target, record)
    return record or {"status": "not_started"}


def start_prebuild(target: Path, force: bool = False) -> dict[str, Any]:
    from android_workflow.cli import read_json, source_changes

    existing = current_status(target)
    if existing.get("status") in {"running", "passed"}:
        return existing
    spec_path = run_dir(target) / "ticket-spec.json"
    spec = read_json(spec_path) if spec_path.is_file() else {}
    if not force and "T7" not in (spec.get("route") or []):
        return {"status": "skipped", "reason": "no device stage in this run's route"}
    if source_changes(target):
        return {"status": "skipped", "reason": "source already changed; a base build is no longer possible"}
    config_path = cache_dir(target) / "project-config.json"
    config = read_json(config_path) if config_path.is_file() else {}
    command = (config.get("commands") or {}).get("build")
    if not command:
        return {"status": "skipped", "reason": "no build command recorded by Setup"}
    log = run_dir(target) / "prebuild.log"
    record = {
        "status": "running",
        "command": command,
        "log": str(log),
        "started_at": time.time(),
        "pid": None,
        "timeout_seconds": (config.get("execution") or {}).get("command_timeout_seconds", 900) * 2,
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
    from android_workflow.cli import source_changes

    target = Path(target_text)
    record = read_record(target)
    started = record.get("started_at") or time.time()
    log = Path(record["log"])
    log.parent.mkdir(parents=True, exist_ok=True)
    try:
        with log.open("w", encoding="utf-8") as handle:
            result = subprocess.run(
                shlex.split(record["command"]) + ["--console=plain"],
                cwd=target, stdout=handle, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                timeout=record.get("timeout_seconds") or 1800, check=False,
            )
        code = result.returncode
    except subprocess.TimeoutExpired:
        code = 124
    except OSError:
        code = 127
    tainted = bool(source_changes(target))
    status = "failed" if code else ("tainted" if tainted else "passed")
    write_record(target, {
        **record,
        "status": status,
        "exit_code": code,
        "duration_seconds": round(time.time() - started, 1),
        "finished_at": time.time(),
        "apks": find_apks(target, started) if code == 0 else [],
        **({"reason": "source changed while the base build ran; rebuild the base before capturing"} if tainted and not code else {}),
    })


def wait_prebuild(target: Path, timeout: float = 1800.0, poll: float = 2.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        record = current_status(target)
        if record.get("status") != "running":
            return record
        if time.monotonic() >= deadline:
            return {**record, "waiting": "timed_out"}
        time.sleep(poll)
