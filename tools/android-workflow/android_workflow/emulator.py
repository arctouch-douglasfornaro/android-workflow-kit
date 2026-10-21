"""Open an existing Android emulator when no device is connected, so the Device stage is not skipped.

`emulator` returns at once: `connected` (a device is already there; nothing is started), `booting`
(an AVD the workflow started, in the background), or `unavailable` with the reason (no SDK, no AVD).
`--wait` blocks until the device has booted. `--stop` closes the emulator only when the workflow
started it; a device the developer connected or opened is never touched. No AVD is ever created and
nothing is downloaded.

The emulator serves the whole machine, so its record lives in `~/.cache/android-workflow/` with the
apps whose runs use it: two runs share it, and it is closed when the last live run lets it go.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

from android_workflow.paths import cache_dir, state_home

RECORD_NAME = "emulator.json"
DEFAULT_BOOT_TIMEOUT = 300.0
PORTS = range(5554, 5586, 2)


def _record_path(_target: Path | None = None) -> Path:
    return state_home() / RECORD_NAME


def _read_record(target: Path) -> dict[str, Any]:
    try:
        value = json.loads(_record_path(target).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_record(target: Path, record: dict[str, Any]) -> None:
    path = _record_path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _clear_record(target: Path) -> None:
    try:
        _record_path(target).unlink()
    except OSError:
        pass


def _overrides(target: Path) -> dict[str, Any]:
    """`device` settings from the project config Setup recorded, then the developer's overrides."""
    merged: dict[str, Any] = {}
    for path in (cache_dir(target) / "project-config.json", target / ".ai" / "android-workflow.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(value, dict) and isinstance(value.get("device"), dict):
            merged.update({key: item for key, item in value["device"].items() if item is not None})
        if isinstance(value, dict) and isinstance((value.get("tools") or {}).get("adb"), str):
            merged["_adb"] = value["tools"]["adb"]
    return merged


def sdk_roots(target: Path) -> list[Path]:
    """Where the Android SDK may be: environment, the app's local.properties, then the usual installs."""
    found: list[Path] = []
    for name in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        if os.environ.get(name):
            found.append(Path(os.environ[name]).expanduser())
    try:
        for line in (target / "local.properties").read_text(encoding="utf-8").splitlines():
            match = re.match(r"\s*sdk\.dir\s*=\s*(.+?)\s*$", line)
            if match:
                found.append(Path(match.group(1).replace("\\:", ":").replace("\\\\", "\\")).expanduser())
    except OSError:
        pass
    home = Path.home()
    found += [home / "Library" / "Android" / "sdk", home / "Android" / "Sdk"]
    unique: list[Path] = []
    for path in found:
        if path.is_dir() and path not in unique:
            unique.append(path)
    return unique


def _tool(target: Path, folder: str, name: str) -> str | None:
    for root in sdk_roots(target):
        candidate = root / folder / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return shutil.which(name)


def adb_path(target: Path) -> str | None:
    configured = _overrides(target).get("_adb")
    if configured and configured != "adb":
        return configured if (Path(configured).is_file() or shutil.which(configured)) else None
    return _tool(target, "platform-tools", "adb")


def emulator_path(target: Path) -> str | None:
    return _tool(target, "emulator", "emulator")


def _run(command: list[str], timeout: float = 15) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(command, text=True, capture_output=True, check=False, timeout=timeout,
                              stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None


def devices(adb: str) -> list[dict[str, str]]:
    """`adb devices`: every serial with its state (`device` is ready to use; `offline` is still booting)."""
    result = _run([adb, "devices"])
    if result is None or result.returncode != 0:
        return []
    found = []
    for line in result.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2:
            found.append({"serial": parts[0], "state": parts[1]})
    return found


def list_avds(emulator: str) -> list[str]:
    result = _run([emulator, "-list-avds"], timeout=30)
    if result is None or result.returncode != 0:
        return []
    # Newer emulators print INFO/WARNING lines before the names; an AVD name has no spaces.
    return [line.strip() for line in result.stdout.splitlines()
            if line.strip() and not re.search(r"\s|\|", line.strip()) and not line.startswith(("INFO", "WARNING", "ERROR"))]


def _pid_alive(pid: Any) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:  # started by this very process (`emulator --wait`): reap it, or an exited one looks alive
        if os.waitpid(pid, os.WNOHANG)[0] == pid:
            return False
    except (ChildProcessError, OSError):
        pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _booted(adb: str, serial: str) -> bool:
    result = _run([adb, "-s", serial, "shell", "getprop", "sys.boot_completed"], timeout=10)
    return bool(result and result.returncode == 0 and result.stdout.strip() == "1")


def _ours(target: Path) -> dict[str, Any]:
    """The emulator the workflow started (for any app), while its process lives; a stale record is dropped."""
    record = _read_record(target)
    if record and not _pid_alive(record.get("pid")):
        _clear_record(target)
        return {}
    return record


def _still_using(user: str) -> bool:
    """Another app's run still needs the emulator while that run is live."""
    from android_workflow.office import run_is_live
    from android_workflow.paths import current_ticket_id, workflow_root

    try:
        app = Path(user)
        ticket = current_ticket_id(app)
        return bool(ticket) and run_is_live(workflow_root(app) / ticket)
    except (OSError, ValueError):
        return False


def status(target: Path) -> dict[str, Any]:
    adb = adb_path(target)
    if not adb:
        return {"status": "unavailable", "reason": "adb not found: install the Android SDK platform-tools or set ANDROID_HOME"}
    ours = _ours(target)
    listed = devices(adb)
    ready = [item["serial"] for item in listed if item["state"] == "device"]
    if ours:
        serial = ours.get("serial")
        state = "ready" if serial in ready and _booted(adb, serial) else "booting"
        return {**ours, "status": state, "started_by_workflow": True, "devices": ready}
    if ready:
        # Several devices: an emulator first (a phone may be in the developer's hand), then the first one.
        serial = next((item for item in ready if item.startswith("emulator-")), ready[0])
        return {"status": "connected", "serial": serial, "devices": ready, "started_by_workflow": False}
    if listed and all(item["state"] == "unauthorized" for item in listed):
        return {"status": "unauthorized", "devices": [item["serial"] for item in listed], "started_by_workflow": False,
                "reason": "a phone is connected but USB debugging is not authorized: accept the prompt on the phone"}
    if listed:
        # A device is there but not usable yet (an emulator still starting, a phone waiting for the
        # USB-debugging prompt): never start a second one next to it.
        return {"status": "booting", "devices": [f"{item['serial']} ({item['state']})" for item in listed],
                "started_by_workflow": False,
                "reason": "a device is connected but not ready; an unauthorized phone needs its USB-debugging prompt accepted"}
    return {"status": "no_device", "reason": "no device connected"}


def start(target: Path, avd: str | None = None, headless: bool | None = None) -> dict[str, Any]:
    """Use the connected device, or start an existing AVD in the background. Returns at once."""
    current = status(target)
    if current.get("started_by_workflow"):
        # Already started for another run (or this one): share it, and remember this app uses it too.
        record = _read_record(target)
        users = list(dict.fromkeys([*(record.get("users") or []), str(target)]))
        _write_record(target, {**record, "users": users})
        return {**current, "users": users}
    if current["status"] in {"connected", "booting", "unavailable", "unauthorized"}:
        return current
    adb = adb_path(target)
    emulator = emulator_path(target)
    if not emulator:
        return {"status": "unavailable", "reason": "no device connected and the Android emulator was not found (set ANDROID_HOME)"}
    avds = list_avds(emulator)
    if not avds:
        return {"status": "unavailable", "reason": "no device connected and no Android Virtual Device exists; "
                "create one in Android Studio (Device Manager) or connect a phone"}
    settings = _overrides(target)
    wanted = avd or settings.get("avd")
    chosen = wanted if wanted in avds else avds[0]
    warnings = [f"AVD {wanted!r} not found; using {chosen!r}"] if wanted and wanted not in avds else []
    taken = {item["serial"] for item in devices(adb or "adb")}
    port = next((number for number in PORTS if f"emulator-{number}" not in taken), None)
    if port is None:
        return {"status": "unavailable", "reason": "every emulator port is taken"}
    if headless is None:
        headless = bool(settings.get("emulator_headless"))
    log = _record_path(target).with_name("emulator.log")
    log.parent.mkdir(parents=True, exist_ok=True)
    command = [emulator, "-avd", chosen, "-port", str(port), "-no-snapshot-save", "-no-boot-anim"]
    if headless:
        command.append("-no-window")
    with log.open("w", encoding="utf-8") as handle:
        try:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=handle, stderr=subprocess.STDOUT,
                                       start_new_session=True)
        except OSError as error:
            return {"status": "unavailable", "reason": f"could not start the emulator: {error}"}
    record = {
        "status": "booting", "avd": chosen, "serial": f"emulator-{port}", "pid": process.pid,
        "started_at": time.time(), "started_by_workflow": True, "headless": headless, "log": str(log),
        "command": command, "users": [str(target)],
    }
    _write_record(target, record)
    return {**record, **({"warnings": warnings} if warnings else {})}


def wait(target: Path, timeout: float | None = None, poll: float = 2.0) -> dict[str, Any]:
    """Block until a device is ready (the one the workflow started, or the connected one)."""
    adb = adb_path(target)
    if not adb:
        return status(target)
    limit = timeout if timeout is not None else float(_overrides(target).get("boot_timeout_seconds") or DEFAULT_BOOT_TIMEOUT)
    deadline = time.monotonic() + limit
    while True:
        record = _read_record(target)
        if record and not _pid_alive(record.get("pid")):
            _clear_record(target)
            tail = ""
            try:
                tail = " ".join(Path(record.get("log", "")).read_text(encoding="utf-8", errors="replace").splitlines()[-3:])
            except OSError:
                pass
            return {"status": "failed", "avd": record.get("avd"), "reason": "the emulator exited while booting" + (f": {tail}" if tail else "")}
        current = status(target)
        if current["status"] in {"ready", "unavailable", "no_device", "unauthorized"}:
            return current
        if current["status"] == "connected":
            serials = current.get("devices") or []
            if len(serials) == 1 and not _booted(adb, serials[0]):
                pass  # a device that is still starting up: keep waiting
            else:
                return current
        if time.monotonic() >= deadline:
            return {**current, "status": "timed_out", "reason": f"the device did not finish booting in {int(limit)}s"}
        time.sleep(poll)


def stop(target: Path, grace: float = 20.0) -> dict[str, Any]:
    """Close the emulator only when this workflow started it and no other live run still uses it."""
    record = _ours(target)
    if not record:
        current = status(target)
        if current["status"] == "connected":
            return {"status": "left_running", "reason": "the workflow did not start this device"}
        return {"status": "not_running"}
    others = [user for user in record.get("users") or [] if user != str(target) and _still_using(user)]
    if others:
        _write_record(target, {**record, "users": others})
        return {"status": "left_running", "reason": "another run still uses this emulator", "users": others}
    adb = adb_path(target)
    pid = int(record["pid"])
    if adb and record.get("serial"):
        _run([adb, "-s", str(record["serial"]), "emu", "kill"], timeout=15)
    deadline = time.monotonic() + grace
    while _pid_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.5)
    if _pid_alive(pid):
        try:
            os.killpg(pid, signal.SIGTERM)  # the group the workflow created when it started the emulator
        except OSError:
            pass
        time.sleep(1)
    _clear_record(target)
    return {"status": "stopped", "avd": record.get("avd"), "serial": record.get("serial")}


EXTRA_NAME = "emulators-extra.json"
DEFAULT_FREE_TIMEOUT = 1800.0


def _extra_path() -> Path:
    return state_home() / EXTRA_NAME


def _extras() -> list[dict[str, Any]]:
    try:
        value = json.loads(_extra_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [item for item in value if isinstance(item, dict) and _pid_alive(item.get("pid"))] if isinstance(value, list) else []


def _save_extras(items: list[dict[str, Any]]) -> None:
    path = _extra_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(items, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _running_avds(adb: str, serials: list[str]) -> set[str]:
    names = set()
    for serial in serials:
        if serial.startswith("emulator-"):
            result = _run([adb, "-s", serial, "emu", "avd", "name"], timeout=10)
            if result and result.returncode == 0 and result.stdout.strip():
                names.add(result.stdout.strip().splitlines()[0].strip())
    return names


def start_extra(target: Path) -> dict[str, Any]:
    """One more emulator for parallel runs (`device.max_emulators` > 1): an AVD that is not running
    yet, else the configured one read-only (`-read-only` lets one AVD run twice). Closed by `stop`
    once no live run uses it."""
    adb, emulator = adb_path(target), emulator_path(target)
    if not adb or not emulator:
        return {"status": "unavailable", "reason": "adb or the emulator was not found"}
    settings = _overrides(target)
    listed = devices(adb)
    running = _running_avds(adb, [item["serial"] for item in listed])
    avds = list_avds(emulator)
    wanted = settings.get("avd")
    idle = [name for name in avds if name not in running]
    chosen = (wanted if wanted in idle else idle[0]) if idle else (wanted if wanted in avds else (avds[0] if avds else None))
    if not chosen:
        return {"status": "unavailable", "reason": "no Android Virtual Device to start"}
    taken = {item["serial"] for item in listed} | {item.get("serial") for item in _extras()}
    port = next((number for number in PORTS if f"emulator-{number}" not in taken), None)
    if port is None:
        return {"status": "unavailable", "reason": "every emulator port is taken"}
    command = [emulator, "-avd", chosen, "-port", str(port), "-no-snapshot-save", "-no-boot-anim"]
    if chosen in running:
        command.append("-read-only")
    if settings.get("emulator_headless"):
        command.append("-no-window")
    log = state_home() / f"emulator-{port}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w", encoding="utf-8") as handle:
        try:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=handle, stderr=subprocess.STDOUT,
                                       start_new_session=True)
        except OSError as error:
            return {"status": "unavailable", "reason": f"could not start the emulator: {error}"}
    record = {"serial": f"emulator-{port}", "pid": process.pid, "avd": chosen, "users": [str(target)],
              "started_at": time.time(), "log": str(log), "command": command}
    _save_extras([*_extras(), record])
    return {**record, "status": "booting", "started_by_workflow": True}


def _stop_extras(target: Path, grace: float = 20.0) -> list[str]:
    """The extra emulators this app's run used: closed when no other live run still uses them."""
    adb = adb_path(target)
    kept, stopped = [], []
    for record in _extras():
        users = [user for user in record.get("users") or [] if user != str(target) and _still_using(user)]
        if users:
            kept.append({**record, "users": users})
            continue
        if adb:
            _run([adb, "-s", str(record["serial"]), "emu", "kill"], timeout=15)
        deadline = time.monotonic() + grace
        while _pid_alive(record.get("pid")) and time.monotonic() < deadline:
            time.sleep(0.5)
        if _pid_alive(record.get("pid")):
            try:
                os.killpg(int(record["pid"]), signal.SIGTERM)
            except OSError:
                pass
        stopped.append(str(record["serial"]))
    _save_extras(kept)
    return stopped


def wait_free(target: Path, timeout: float | None = None, poll: float = 5.0) -> dict[str, Any]:
    """Block until a ready device is free for this run (no other run's lease on it), starting one
    more emulator first when `device.max_emulators` allows it. Returns the serial to use."""
    from android_workflow.locks import free_serials, lease_of
    from android_workflow.paths import run_dir

    run = run_dir(target)
    limit = timeout if timeout is not None else DEFAULT_FREE_TIMEOUT
    deadline = time.monotonic() + limit
    adb = adb_path(target)
    started_extra = False
    while True:
        current = status(target)
        serials = [str(current["serial"])] if current.get("serial") else []
        serials += [str(item) for item in current.get("devices") or [] if str(item) not in serials]
        ready = [serial for serial in serials if adb and _booted(adb, serial)]
        free = free_serials(ready, run)
        if free:
            return {**current, "status": "free", "serial": free[0]}
        maximum = int(_overrides(target).get("max_emulators") or 1)
        if not started_extra and maximum > 1 and len(_extras()) + 1 < maximum:
            extra = start_extra(target)
            started_extra = True
            if extra.get("status") != "booting":
                current = {**current, "extra": extra}
        if time.monotonic() >= deadline:
            holders = {serial: Path(str((lease_of(serial) or {}).get("run") or "?")).name for serial in serials}
            return {"status": "busy", "serial": None, "holders": holders,
                    "reason": f"every device stayed in use by another run for {int(limit)}s"}
        time.sleep(poll)


def dispatch_emulator(target: Path, args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    if args.stop:
        result = stop(target)
        extras = _stop_extras(target)
        return ({**result, "extras_stopped": extras} if extras else result), 0
    if args.status:
        return status(target), 0
    if getattr(args, "wait_free", False):
        result = wait_free(target, timeout=args.timeout)
        return result, 0 if result.get("status") == "free" else 1
    result = start(target, avd=args.avd, headless=True if args.headless else None)
    if args.wait and result.get("status") in {"booting", "connected"}:
        result = wait(target, timeout=args.timeout)
    usable = {"connected", "ready", "booting"} if not args.wait else {"connected", "ready"}
    return result, 0 if result.get("status") in usable else 1
