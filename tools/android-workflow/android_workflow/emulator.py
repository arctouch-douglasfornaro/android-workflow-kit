"""Give each run its own device: reuse one that is open and free, else start one more emulator.

A run leases one device (`locks`) the first time it needs one and keeps it until `finish`, so two
runs never install their builds on one device. `claim` is the one entry point:

* the run already holds a device → that one (a leased emulator that died is dropped and replaced);
* a ready device nobody leases (an emulator the developer or another run opened, a phone) → leased;
* else, below `device.max_emulators` (default 2) emulators the workflow started → one more, started
  for this run and leased to it while it boots (an AVD that is not running, else the configured AVD
  again with `-read-only`);
* else `busy`, naming who holds each device; `unavailable` with the reason when there is no SDK or AVD.

`--stop` gives the run's device back and closes the emulators the workflow started that no lease
holds and no other live run is waiting for; a device the developer connected or opened is never
closed. No AVD is ever created and nothing is downloaded. The pool record lives in
`~/.cache/android-workflow/` because the emulators serve the whole machine.
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

POOL_NAME = "emulators.json"
LEGACY_NAMES = ("emulator.json", "emulators-extra.json")
DEFAULT_BOOT_TIMEOUT = 300.0
DEFAULT_FREE_TIMEOUT = 1800.0
DEFAULT_MAX_EMULATORS = 2
PORTS = range(5554, 5586, 2)
NO_DEVICE_CHECK_ENV = "ANDROID_WORKFLOW_NO_DEVICE_CHECK"  # the test suites: never touch a real device


def _record_path(_target: Path | None = None) -> Path:
    return state_home() / POOL_NAME


def _read_pool() -> list[dict[str, Any]]:
    """The emulators the workflow started that still run (records of older kits are merged in)."""
    items: list[dict[str, Any]] = []
    for name in (POOL_NAME, *LEGACY_NAMES):
        try:
            value = json.loads((state_home() / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        items += [value] if isinstance(value, dict) and value else [item for item in value if isinstance(item, dict)] \
            if isinstance(value, list) else []
    unique: dict[str, dict[str, Any]] = {}
    for item in items:
        if item.get("serial") and _pid_alive(item.get("pid")):
            unique.setdefault(str(item["serial"]), item)
    return list(unique.values())


def _write_pool(items: list[dict[str, Any]]) -> None:
    path = _record_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not items:
        for name in (POOL_NAME, *LEGACY_NAMES):
            try:
                (state_home() / name).unlink()
            except OSError:
                pass
        return
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(items, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    for name in LEGACY_NAMES:
        try:
            (state_home() / name).unlink()
        except OSError:
            pass


def _pool_lock():
    from android_workflow.locks import exclusive

    return exclusive(state_home() / "locks" / "emulators.lock", poll=0.1)


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


def _run_of(target: Path) -> Path | None:
    from android_workflow.paths import run_dir

    try:
        return run_dir(target)
    except (OSError, ValueError):
        return None


def _max_emulators(target: Path) -> int:
    try:
        return max(int(_overrides(target).get("max_emulators") or DEFAULT_MAX_EMULATORS), 1)
    except (TypeError, ValueError):
        return DEFAULT_MAX_EMULATORS


def _order(serials: list[str]) -> list[str]:
    """Emulators first (a phone may be in the developer's hand), then the rest, in adb's order."""
    return sorted(serials, key=lambda serial: not serial.startswith("emulator-"))


def status(target: Path) -> dict[str, Any]:
    """What this run has or could use, without starting or leasing anything."""
    from android_workflow.locks import held_by

    adb = adb_path(target)
    if not adb:
        return {"status": "unavailable", "reason": "adb not found: install the Android SDK platform-tools or set ANDROID_HOME"}
    pool = {str(item["serial"]): item for item in _read_pool()}
    listed = devices(adb)
    ready = [item["serial"] for item in listed if item["state"] == "device"]
    run = _run_of(target)
    for serial in held_by(run) if run else []:
        record = pool.get(serial)
        if serial in ready and _booted(adb, serial):
            return {**(record or {}), "status": "ready" if record else "connected", "serial": serial,
                    "devices": ready, "started_by_workflow": bool(record), "leased": True}
        if record or any(item["serial"] == serial for item in listed):
            return {**(record or {}), "status": "booting", "serial": serial, "devices": ready,
                    "started_by_workflow": bool(record), "leased": True}
    if ready:
        serial = _order(ready)[0]
        record = pool.get(serial)
        state = ("ready" if _booted(adb, serial) else "booting") if record else "connected"
        return {**(record or {}), "status": state, "serial": serial, "devices": ready, "started_by_workflow": bool(record)}
    if pool:
        record = next(iter(pool.values()))
        return {**record, "status": "booting", "started_by_workflow": True, "devices": ready}
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


def _pick_avd(target: Path, adb: str, emulator: str, avd: str | None) -> tuple[str | None, bool, list[str]]:
    """(AVD, read_only, warnings): the configured AVD, else one that is not running yet; an AVD already
    running starts again `-read-only` (one AVD can run twice that way)."""
    avds = list_avds(emulator)
    if not avds:
        return None, False, []
    running = _running_avds(adb, [item["serial"] for item in devices(adb)]) | \
        {str(item.get("avd")) for item in _read_pool()}
    wanted = avd or _overrides(target).get("avd")
    warnings = []
    if wanted and wanted not in avds:
        warnings.append(f"AVD {wanted!r} not found; set device.avd in .ai/android-workflow.json to one of {avds}")
        wanted = None
    if not wanted:
        idle = [name for name in avds if name not in running]
        wanted = idle[0] if idle else avds[0]
        if len(avds) > 1:
            warnings.append(f"no device.avd configured: using {wanted!r}; pin one in .ai/android-workflow.json")
    return wanted, wanted in running, warnings


def _launch(target: Path, run: Path | None, avd: str | None = None, headless: bool | None = None) -> dict[str, Any]:
    """Start one more emulator in the background and add it to the pool. Returns at once."""
    adb = adb_path(target)
    emulator = emulator_path(target)
    if not adb:
        return {"status": "unavailable", "reason": "adb not found: install the Android SDK platform-tools or set ANDROID_HOME"}
    if not emulator:
        return {"status": "unavailable", "reason": "no device connected and the Android emulator was not found (set ANDROID_HOME)"}
    chosen, read_only, warnings = _pick_avd(target, adb, emulator, avd)
    if not chosen:
        return {"status": "unavailable", "reason": "no device connected and no Android Virtual Device exists; "
                "create one in Android Studio (Device Manager) or connect a phone"}
    pool = _read_pool()
    taken = {item["serial"] for item in devices(adb)} | {str(item.get("serial")) for item in pool}
    port = next((number for number in PORTS if f"emulator-{number}" not in taken), None)
    if port is None:
        return {"status": "unavailable", "reason": "every emulator port is taken"}
    if headless is None:
        headless = bool(_overrides(target).get("emulator_headless"))
    log = state_home() / f"emulator-{port}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    command = [emulator, "-avd", chosen, "-port", str(port), "-no-snapshot-save", "-no-boot-anim"]
    if read_only:
        command.append("-read-only")
    if headless:
        command.append("-no-window")
    with log.open("w", encoding="utf-8") as handle:
        try:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=handle, stderr=subprocess.STDOUT,
                                       start_new_session=True)
        except OSError as error:
            return {"status": "unavailable", "reason": f"could not start the emulator: {error}"}
    record = {"avd": chosen, "serial": f"emulator-{port}", "pid": process.pid, "started_at": time.time(),
              "headless": headless, "log": str(log), "command": command,
              **({"started_for": str(run)} if run else {})}
    _write_pool([*pool, record])
    return {**record, "status": "booting", "started_by_workflow": True, **({"warnings": warnings} if warnings else {})}


def claim(target: Path, record: bool = True, avd: str | None = None, headless: bool | None = None) -> dict[str, Any]:
    """The device this run uses: `ready`/`connected` (leased, booted), `booting` (leased, starting),
    `busy` (every device is another run's and no more emulators may start), or `unavailable` /
    `unauthorized` / `no_device` with the reason. With `record=False` nothing is started or leased."""
    from android_workflow.locks import acquire, free_serials, held_by, leases, release, run_label, want

    run = _run_of(target)
    if os.environ.get(NO_DEVICE_CHECK_ENV):
        return _claim_reported(target, run, record)
    adb = adb_path(target)
    if not adb:
        return {"status": "unavailable", "reason": "adb not found: install the Android SDK platform-tools or set ANDROID_HOME"}
    if run is None:
        return start(target, avd=avd, headless=headless)
    with _pool_lock():
        if record:
            want(run)
        pool = {str(item["serial"]): item for item in _read_pool()}
        listed = devices(adb)
        ready = [item["serial"] for item in listed if item["state"] == "device"]
        for serial in held_by(run):
            if serial in ready and _booted(adb, serial):
                return {**pool.get(serial, {}), "status": "ready" if serial in pool else "connected", "serial": serial,
                        "devices": ready, "started_by_workflow": serial in pool, "leased": True}
            if serial in pool or any(item["serial"] == serial for item in listed):
                return {**pool.get(serial, {}), "status": "booting", "serial": serial, "devices": ready,
                        "started_by_workflow": serial in pool, "leased": True}
            if record:
                release(run, serial=serial)  # its emulator is gone: lease another one below
        free = [serial for serial in _order(free_serials(ready, run)) if _booted(adb, serial)]
        if free:
            serial = free[0]
            if record:
                acquire(serial, run)
            return {**pool.get(serial, {}), "status": "ready" if serial in pool else "connected", "serial": serial,
                    "devices": ready, "started_by_workflow": serial in pool, "leased": record}
        held = leases()
        idle_booting = [serial for serial in pool if serial not in held]  # every booted free one was taken above
        if idle_booting:
            if record:
                acquire(idle_booting[0], run)
            return {**pool[idle_booting[0]], "status": "booting", "started_by_workflow": True, "leased": record}
        unready = [item for item in listed if item["state"] != "device" and item["serial"] not in pool]
        if unready and not held and all(item["state"] != "unauthorized" for item in unready):
            return {"status": "booting", "devices": [f"{item['serial']} ({item['state']})" for item in unready],
                    "started_by_workflow": False, "reason": "a device is connected but not ready yet"}
        maximum = _max_emulators(target)
        holders = {serial: run_label((held.get(serial) or {}).get("run") or "?") for serial in [*ready, *pool]
                   if serial in held}
        if len(pool) < maximum:
            if not record:
                return {"status": "would_start", "reason": "no free device: one more emulator starts for this run",
                        "holders": holders}
            started = _launch(target, run, avd=avd, headless=headless)
            if started.get("status") == "booting":
                acquire(str(started["serial"]), run)
                return {**started, "leased": True, "holders": holders}
            if not holders:
                return started
            return {"status": "busy", "holders": holders, "reason": f"could not start one more emulator: {started.get('reason')}"}
        if not holders and listed and all(item["state"] == "unauthorized" for item in listed):
            return {"status": "unauthorized", "devices": [item["serial"] for item in listed], "started_by_workflow": False,
                    "reason": "a phone is connected but USB debugging is not authorized: accept the prompt on the phone"}
        return {"status": "busy", "holders": holders,
                "reason": f"every device is in use and {len(pool)} of device.max_emulators={maximum} emulators already run"}


def _claim_reported(target: Path, run: Path | None, record: bool) -> dict[str, Any]:
    """`claim` with device checks off (`ANDROID_WORKFLOW_NO_DEVICE_CHECK`, the test suites): the devices
    are what `status` reports, leases work as usual, and no emulator is ever started."""
    from android_workflow.locks import acquire, free_serials, lease_of, run_label

    current = status(target)
    if current.get("status") not in {"connected", "ready"}:
        return current
    serials = [str(current["serial"])] if current.get("serial") else []
    serials += [str(serial) for serial in current.get("devices") or [] if str(serial) not in serials]
    free = free_serials(serials, run) if run else serials
    if free:
        if record and run:
            acquire(free[0], run)
        return {**current, "serial": free[0], "leased": bool(record and run)}
    return {"status": "busy", "holders": {serial: run_label((lease_of(serial) or {}).get("run") or "?") for serial in serials},
            "reason": "every device is in use (device checks are off: no emulator starts)"}


def start(target: Path, avd: str | None = None, headless: bool | None = None) -> dict[str, Any]:
    """`CLI emulator`: the run's device (see `claim`). Outside a run: a connected device, else one
    emulator the workflow started, else a new one."""
    if _run_of(target) is not None:
        return claim(target, avd=avd, headless=headless)
    current = status(target)
    if current["status"] != "no_device":
        return current
    with _pool_lock():
        return _launch(target, None, avd=avd, headless=headless)


def wait(target: Path, timeout: float | None = None, poll: float = 2.0) -> dict[str, Any]:
    """Block until the run's device (or, outside a run, any device) has booted."""
    adb = adb_path(target)
    if not adb:
        return status(target)
    limit = timeout if timeout is not None else float(_overrides(target).get("boot_timeout_seconds") or DEFAULT_BOOT_TIMEOUT)
    deadline = time.monotonic() + limit
    watched: dict[str, Any] = {}
    while True:
        current = status(target)
        serial = current.get("serial")
        if current.get("started_by_workflow") and serial:
            watched = {**current}
        elif watched and not _pid_alive(watched.get("pid")):
            tail = ""
            try:
                tail = " ".join(Path(str(watched.get("log", ""))).read_text(encoding="utf-8", errors="replace").splitlines()[-3:])
            except OSError:
                pass
            return {"status": "failed", "avd": watched.get("avd"), "serial": watched.get("serial"),
                    "reason": "the emulator exited while booting" + (f": {tail}" if tail else "")}
        if current["status"] in {"ready", "unavailable", "no_device", "unauthorized"}:
            if current["status"] == "no_device" and watched:
                pass  # the emulator it started vanished from adb: the pid check above reports it
            else:
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


def _kill(adb: str | None, record: dict[str, Any], grace: float) -> None:
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


def stop(target: Path, grace: float = 20.0) -> dict[str, Any]:
    """Give the run's device back; close the emulators the workflow started that no lease holds, unless
    another live run still has a Device stage ahead (it reuses one). A device the developer opened or
    connected is never closed."""
    from android_workflow.locks import leases, release, run_label, unwant, wanting

    run = _run_of(target)
    released = release(run) if run else []
    if run:
        unwant(run)
    adb = adb_path(target)
    with _pool_lock():
        pool = _read_pool()
        held = leases()
        holders = {str(lease.get("run")) for lease in held.values()}
        waiting = [item for item in wanting(exclude=run) if item not in holders]  # still needs a device of its own
        kept, stopped, reasons = [], [], {}
        for record in pool:
            serial = str(record["serial"])
            if serial in held:
                kept.append(record)
                reasons[serial] = f"leased to {run_label(held[serial].get('run') or '?')}"
                continue
            if waiting:
                kept.append(record)
                reasons[serial] = "kept for " + ", ".join(run_label(item) for item in waiting)
                continue
            _kill(adb, record, grace)
            stopped.append(serial)
        _write_pool(kept)
    result: dict[str, Any] = {"released": released} if released else {}
    if stopped:
        return {**result, "status": "stopped", "stopped": stopped, "serial": stopped[0],
                **({"left_running": reasons} if reasons else {})}
    if kept:
        return {**result, "status": "left_running", "reason": "; ".join(f"{serial}: {why}" for serial, why in reasons.items()),
                "left_running": reasons}
    current = status(target)
    if current["status"] in {"connected", "ready"}:
        return {**result, "status": "left_running", "reason": "the workflow did not start this device"}
    return {**result, "status": "not_running"}


def _running_avds(adb: str, serials: list[str]) -> set[str]:
    names = set()
    for serial in serials:
        if serial.startswith("emulator-"):
            result = _run([adb, "-s", serial, "emu", "avd", "name"], timeout=10)
            if result and result.returncode == 0 and result.stdout.strip():
                names.add(result.stdout.strip().splitlines()[0].strip())
    return names


def wait_free(target: Path, timeout: float | None = None, poll: float = 5.0) -> dict[str, Any]:
    """Block until this run has a booted device of its own (`claim` until it is `ready`/`connected`):
    another run gives one back, or one more emulator starts and boots. Returns the serial to use."""
    limit = timeout if timeout is not None else DEFAULT_FREE_TIMEOUT
    deadline = time.monotonic() + limit
    while True:
        current = claim(target)
        if current.get("status") in {"ready", "connected"}:
            return {**current, "status": "free"}
        if current.get("status") in {"unavailable", "unauthorized", "no_device"}:
            return current
        if time.monotonic() >= deadline:
            return {**current, "status": "busy", "serial": None,
                    "reason": f"no device was free for this run in {int(limit)}s: {current.get('reason') or current.get('status')}"}
        time.sleep(poll)


def dispatch_emulator(target: Path, args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    if args.stop:
        return stop(target), 0
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
