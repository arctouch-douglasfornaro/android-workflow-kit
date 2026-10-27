"""Machine-wide coordination between runs, so two tickets can run side by side on one machine.

Two things on a developer's machine are shared by every run of every app:

* **Build slots.** A cold Gradle build of a large app takes a daemon of several GB and every core;
  three at once (two runs' gates and a base build) starve each other until a test worker or the
  emulator dies. Heavy builds (the gate, the base build, the Device install) take one of
  `execution.max_parallel_builds` slots (default 2, `ANDROID_WORKFLOW_BUILD_SLOTS` overrides) and
  wait for a free one.
* **Device leases.** Two runs installing the same debug package on one emulator overwrite each
  other's build mid-check. A run leases one device the first time it needs one (`before` or
  `after`) and keeps it until `finish`: another run takes a free device, or one more emulator, or
  waits. A command (an install, a replay) adds its pid to the lease while it runs, so a lease never
  ends under a command that is still writing to the device.

Everything lives under `state_home()`; a lease whose holder is gone (its run ended or went idle, and
no command of it still runs) is dropped by the next reader.
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
LEASE_IDLE_SECONDS = 3 * 60 * 60  # a run untouched this long (a session closed mid-run) lets its device go


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


def _run_holds_device(run: str, since: float) -> bool:
    """The holder run still needs its device: not finished, and touched in the last `LEASE_IDLE_SECONDS`."""
    try:
        state = json.loads((Path(run) / "run-state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(state, dict) or state.get("status") == "completed":
        return False
    touched = max(float(state.get("updated_at") or 0), since)
    return time.time() - touched < LEASE_IDLE_SECONDS


def _pids(lease: dict[str, Any]) -> list[int]:
    found = [int(pid) for pid in lease.get("pids") or [] if isinstance(pid, int)]
    if isinstance(lease.get("pid"), int):  # a lease written before `pids`
        found.append(int(lease["pid"]))
    return list(dict.fromkeys(found))


def _stale(lease: dict[str, Any]) -> bool:
    if any(_pid_alive(pid) for pid in _pids(lease)):
        return False  # a command of the run still writes to the device
    if lease.get("mode") == "command":
        return True
    return not _run_holds_device(str(lease.get("run") or ""), float(lease.get("since") or 0))


def lease_of(serial: str) -> dict[str, Any] | None:
    with _leases() as leases:
        return leases.get(serial)


def leases() -> dict[str, dict[str, Any]]:
    """Every live lease, by serial (for the pool and the office)."""
    with _leases() as live:
        return dict(live)


def held_by(run: Path | str) -> list[str]:
    """The serials `run` holds."""
    run = str(run)
    with _leases() as live:
        return [serial for serial, lease in live.items() if lease.get("run") == run]


def acquire(serial: str, run: Path | str, mode: str = "run", pid: int | None = None) -> dict[str, Any] | None:
    """Take `serial` for `run`. Returns None when another run holds it, else the lease.

    A run's lease lives until the run finishes (or goes idle); a command passes its `pid`, which joins
    the lease and keeps it alive while the process runs, even past the run's own release."""
    run = str(run)
    with _leases() as live:
        held = live.get(serial)
        if held and held.get("run") != run:
            return None
        lease = dict(held) if held else {"run": run, "mode": "command" if pid else mode, "since": time.time()}
        lease.pop("pid", None)
        pids = [item for item in _pids(held or {}) if _pid_alive(item)]
        if pid:
            pids = list(dict.fromkeys([*pids, pid]))
        else:
            lease.update({"mode": "run", "since": time.time()})
        if pids:
            lease["pids"] = pids
        else:
            lease.pop("pids", None)
        live[serial] = lease
        return lease


def release(run: Path | str, serial: str | None = None, pid: int | None = None) -> list[str]:
    """Give back what `run` holds (one serial, or all). With `pid`, only that command's part.

    A serial a command of the run still writes to stays leased to that command until it exits;
    it is not in the returned list."""
    run = str(run)
    dropped = []
    with _leases() as live:
        for key in list(live):
            lease = live[key]
            if lease.get("run") != run or (serial and key != serial):
                continue
            pids = [item for item in _pids(lease) if _pid_alive(item) and item != pid]
            if pid is not None and lease.get("mode") == "run":
                live[key] = {**{k: v for k, v in lease.items() if k not in {"pid", "pids"}}, **({"pids": pids} if pids else {})}
                continue
            if pids:
                live[key] = {**{k: v for k, v in lease.items() if k != "pid"}, "mode": "command", "pids": pids}
                continue
            dropped.append(key)
            del live[key]
    return dropped


def free_serials(serials: list[str], run: Path | str) -> list[str]:
    """`serials` that nobody else leases (the run's own lease counts as free), in their order."""
    run = str(run)
    with _leases() as live:
        return [serial for serial in serials if (live.get(serial) or {}).get("run") in (None, run)]


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


# --- runs that will need a device ----------------------------------------------------------------

def _wanted_path() -> Path:
    return _locks_dir() / "device-wanted.json"


@contextlib.contextmanager
def _wanted() -> Iterator[dict[str, float]]:
    with exclusive(_locks_dir() / "device-wanted.lock", poll=0.1):
        path = _wanted_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        live = {run: float(since) for run, since in (data.items() if isinstance(data, dict) else [])
                if isinstance(since, (int, float)) and _run_holds_device(run, float(since))}
        yield live
        if live != data:
            temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
            temporary.write_text(json.dumps(live, indent=2) + "\n", encoding="utf-8")
            os.replace(temporary, path)


def want(run: Path | str) -> None:
    """`run` has a Device stage ahead: an emulator nobody leases is kept open for it."""
    with _wanted() as wanted:
        wanted.setdefault(str(run), time.time())


def unwant(run: Path | str) -> None:
    with _wanted() as wanted:
        wanted.pop(str(run), None)


def wanting(exclude: Path | str | None = None) -> list[str]:
    """Live runs that still have a Device stage ahead (other than `exclude`)."""
    with _wanted() as wanted:
        return [run for run in wanted if run != str(exclude or "")]


def run_label(run: Path | str) -> str:
    """`<worktree>/<ticket>` for a run folder (`<app>/.ai/workflow/<ticket>`): parallel worktrees of one
    ticket are told apart."""
    path = Path(str(run))
    app = path.parents[2].name if len(path.parents) >= 3 and path.parent.name == "workflow" else ""
    return f"{app}/{path.name}" if app else path.name
