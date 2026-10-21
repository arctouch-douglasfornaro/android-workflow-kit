"""Machine-wide coordination between runs, so two tickets can run side by side on one machine.

Two things on a developer's machine are shared by every run of every app:

* **Build slots.** A cold Gradle build of a large app takes a daemon of several GB and every core;
  three at once (two runs' gates and a base build) starve each other until a test worker or the
  emulator dies. Heavy builds (the gate, the base build, the Device install) take one of
  `execution.max_parallel_builds` slots (default 2, `ANDROID_WORKFLOW_BUILD_SLOTS` overrides) and
  wait for a free one.
* **Device leases.** Two runs installing the same debug package on one emulator overwrite each
  other's build mid-check. A Device agent (or a replay) works on a serial only while its run holds
  that serial's lease; another run waits for it, or uses another device.

Everything lives under `state_home()`; a lease whose holder is gone (process dead, run no longer on
the device, or older than `LEASE_MAX_SECONDS`) is dropped by the next reader.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Iterator

from android_workflow.paths import state_home

SLOTS_ENV = "ANDROID_WORKFLOW_BUILD_SLOTS"
DEFAULT_SLOTS = 2
LEASE_MAX_SECONDS = 45 * 60  # a Device round is budgeted at minutes; a lease this old is left behind


def _locks_dir() -> Path:
    path = state_home() / "locks"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _pid_alive(pid: Any) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@contextlib.contextmanager
def exclusive(path: Path, on_wait: Callable[[dict[str, Any]], None] | None = None,
              poll: float = 1.0) -> Iterator[None]:
    """Hold `path` exclusively; `on_wait` is told once who holds it when we have to wait."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        told = False
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if on_wait and not told:
                    told = True
                    on_wait(_holder(path))
                time.sleep(poll)
        try:
            _write_holder(handle, {"pid": os.getpid(), "since": time.time()})
            yield
        finally:
            _write_holder(handle, {})
            fcntl.flock(handle, fcntl.LOCK_UN)


def _write_holder(handle: Any, value: dict[str, Any]) -> None:
    try:
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps(value))
        handle.flush()
    except OSError:
        pass


def _holder(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


# --- build slots -------------------------------------------------------------------------------

def build_slots(config: dict[str, Any] | None = None) -> int:
    raw = os.environ.get(SLOTS_ENV) or ((config or {}).get("execution") or {}).get("max_parallel_builds")
    try:
        value = int(raw) if raw not in (None, "") else DEFAULT_SLOTS
    except (TypeError, ValueError):
        value = DEFAULT_SLOTS
    return max(value, 1)


def slot_holders() -> list[dict[str, Any]]:
    """Who holds a build slot now (live holders only), for status and the office."""
    held = []
    for path in sorted(_locks_dir().glob("build-slot-*.lock")):
        holder = _holder(path)
        if holder and _pid_alive(holder.get("pid")):
            held.append({**holder, "slot": path.stem})
    return held


@contextlib.contextmanager
def build_slot(label: str, config: dict[str, Any] | None = None,
               on_wait: Callable[[list[dict[str, Any]]], None] | None = None, poll: float = 2.0) -> Iterator[dict[str, Any]]:
    """Run a heavy build in one of the machine's build slots; wait while every slot is taken.

    Yields `{slot, waited_seconds}`. `on_wait` is called once, with the current holders, when it waits."""
    slots = build_slots(config)
    began = time.monotonic()
    told = False
    handles = []
    try:
        while True:
            for index in range(slots):
                path = _locks_dir() / f"build-slot-{index}.lock"
                handle = path.open("a+")
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    handle.close()
                    continue
                handles.append(handle)
                _write_holder(handle, {"pid": os.getpid(), "label": label, "since": time.time()})
                yield {"slot": index, "waited_seconds": round(time.monotonic() - began, 1)}
                return
            if on_wait and not told:
                told = True
                on_wait(slot_holders())
            time.sleep(poll)
    finally:
        for handle in handles:
            _write_holder(handle, {})
            fcntl.flock(handle, fcntl.LOCK_UN)
            handle.close()


# --- device leases -----------------------------------------------------------------------------

def _leases_path() -> Path:
    return _locks_dir() / "device-leases.json"


@contextlib.contextmanager
def _leases() -> Iterator[dict[str, Any]]:
    """Read-modify-write of every lease under one lock."""
    with exclusive(_locks_dir() / "device-leases.lock", poll=0.1):
        path = _leases_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        live = {serial: lease for serial, lease in data.items() if isinstance(lease, dict) and not _stale(lease)}
        yield live
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temporary.write_text(json.dumps(live, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)


def _run_on_device(run: str) -> bool:
    """The holder run still has its Device stage open (an agent is on the device)."""
    try:
        state = json.loads((Path(run) / "run-state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return ((state.get("stages") or {}).get("T7") or {}).get("status") == "started"


def _stale(lease: dict[str, Any]) -> bool:
    if time.time() - float(lease.get("since") or 0) > LEASE_MAX_SECONDS:
        return True
    pid = lease.get("pid")
    if pid is not None:  # a command (a replay) holds it while it runs
        return not _pid_alive(pid)
    return not _run_on_device(str(lease.get("run") or ""))


def lease_of(serial: str) -> dict[str, Any] | None:
    with _leases() as leases:
        return leases.get(serial)


def acquire(serial: str, run: Path | str, mode: str = "agent", pid: int | None = None) -> dict[str, Any] | None:
    """Take `serial` for `run`. Returns None when another run holds it, else the lease.

    An agent's lease lives while its run's Device stage is open; a command passes its `pid`, and its
    lease lives while that process does. A run that already holds the serial keeps it (renewed)."""
    run = str(run)
    with _leases() as leases:
        held = leases.get(serial)
        if held and held.get("run") != run:
            return None
        lease = {"run": run, "mode": mode, "since": time.time(), **({"pid": pid} if pid else {})}
        if held and held.get("pid") is None and pid is not None:
            lease = held  # a command inside an agent's round: the agent's lease already covers it
        leases[serial] = lease
        return lease


def release(run: Path | str, serial: str | None = None, pid: int | None = None) -> list[str]:
    """Drop the leases `run` holds (one serial, or all; with `pid`, only that command's)."""
    run = str(run)
    dropped = []
    with _leases() as leases:
        for key in list(leases):
            lease = leases[key]
            if lease.get("run") != run or (serial and key != serial):
                continue
            if pid is not None and lease.get("pid") != pid:
                continue
            dropped.append(key)
            del leases[key]
    return dropped


def free_serials(serials: list[str], run: Path | str) -> list[str]:
    """`serials` that nobody else leases (the run's own lease counts as free), in their order."""
    run = str(run)
    with _leases() as leases:
        return [serial for serial in serials if (leases.get(serial) or {}).get("run") in (None, run)]


@contextlib.contextmanager
def device_lease(serial: str, run: Path | str, on_wait: Callable[[dict[str, Any]], None] | None = None,
                 poll: float = 5.0, timeout: float | None = None) -> Iterator[bool]:
    """A command's lease for its whole run: waits while another run holds the serial.

    Yields False when `timeout` passed first (the caller reports it and does not touch the device)."""
    pid = os.getpid()
    deadline = time.monotonic() + timeout if timeout is not None else None
    told = False
    while acquire(serial, run, mode="command", pid=pid) is None:
        if on_wait and not told:
            told = True
            on_wait(lease_of(serial) or {})
        if deadline is not None and time.monotonic() >= deadline:
            yield False
            return
        time.sleep(poll)
    try:
        yield True
    finally:
        release(run, serial=serial, pid=pid)
