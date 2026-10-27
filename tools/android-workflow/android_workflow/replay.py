"""Run the Device agent's saved Maestro flows by code, on either build.

The Device `before` agent finds the way to the changed screen once, while the Implementer works, and
saves it as flows in `RUN/media/flows/`. Each flow names its own captures (`takeScreenshot: <name>`,
`startRecording: <name>`): that name is the evidence name, so the same flow files its output under
`media/before/` on the base build and under `media/after/` on the new one. The `after` Device agent
then only replays and judges; a flow that fails sends it back to driving by hand.

Flow files: `*.yaml` run with Maestro; `_*.yaml` are helpers a flow pulls in with `runFlow` and never
run alone; a `<flow>.sh` next to `<flow>.yaml` runs instead of it, for what Maestro cannot do (turn
the network off at a marker) and gets `AW_SERIAL`, `AW_MAESTRO`, `AW_OUT`, `AW_FLOW`, `AW_PHASE`.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from android_workflow.paths import cache_dir, media_dir, run_dir

REPLAY_FILE = "replay.json"
AFTER_BUILD_FILE = "after-build.json"
INSTALLABLE = frozenset({"passed", "no_build_command", "no_apk"})  # no APK to `adb install`: the install task builds
FLOW_TIMEOUT_SECONDS = 180
AFTER_BUDGET_SECONDS = 300
CAPTURE_RE = re.compile(r"^\s*-?\s*(?:takeScreenshot|startRecording)\s*:\s*['\"]?([^'\"\n#]+?)['\"]?\s*(?:#.*)?$", re.M)


def maestro_path() -> str | None:
    found = shutil.which("maestro")
    if found:
        return found
    default = Path.home() / ".maestro" / "bin" / "maestro"
    return str(default) if default.is_file() and os.access(default, os.X_OK) else None


def flows(target: Path) -> list[Path]:
    """What a replay runs, in name order: flows, minus `_helpers` and any flow a same-name `.sh` replaces."""
    folder = media_dir(target) / "flows"
    if not folder.is_dir():
        return []
    found = [path for pattern in ("*.yaml", "*.yml", "*.sh") for path in folder.glob(pattern)
             if not path.name.startswith("_")]
    scripts = {path.stem for path in found if path.suffix == ".sh"}
    return sorted((path for path in found if path.suffix == ".sh" or path.stem not in scripts), key=lambda path: path.name)


def declared_names(flow: Path) -> list[str]:
    """The evidence names a flow captures: the last path part of each takeScreenshot/startRecording."""
    source = flow if flow.suffix != ".sh" else next((flow.with_suffix(ext) for ext in (".yaml", ".yml")
                                                    if flow.with_suffix(ext).is_file()), None)
    if source is None:
        return []
    try:
        text = source.read_text(encoding="utf-8")
    except OSError:
        return []
    return sorted({re.sub(r"\.(png|mp4|webm)$", "", Path(raw.strip()).name) for raw in CAPTURE_RE.findall(text)})


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tail(path: Path, lines: int = 15) -> str:
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return ""


def install(target: Path, config: dict[str, Any], serial: str, log: Path) -> tuple[bool, str]:
    """The Setup's Gradle install task on `serial`: only when the project has no `build` command, since
    it compiles while it holds the device."""
    command = (config.get("commands") or {}).get("install")
    if not command:
        return False, "no install command recorded by Setup"
    from android_workflow.locks import build_slot

    timeout = int((config.get("execution") or {}).get("command_timeout_seconds") or 900) * 2
    env = {**os.environ, "ANDROID_SERIAL": serial}
    try:
        with log.open("w", encoding="utf-8") as handle, build_slot(f"device install ({target.name})", config):
            result = subprocess.run(shlex.split(command) + ["--console=plain"], cwd=target, env=env, stdout=handle,
                                    stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return False, f"the install timed out after {timeout}s"
    except OSError as error:
        return False, f"could not run the install: {error}"
    if result.returncode != 0:
        return False, f"the install failed (exit {result.returncode}): {_tail(log, 5)}"
    return True, command


def _app_module(config: dict[str, Any]) -> str:
    module = str((config.get("device") or {}).get("app_module") or "").strip(":")
    if module:
        return module.split(":")[-1]
    install_command = str((config.get("commands") or {}).get("install") or "")
    return install_command.split(":")[-2] if install_command.count(":") >= 2 else ""


def _pick_apk(apks: list[Path], config: dict[str, Any]) -> Path | None:
    module = _app_module(config)
    preferred = [path for path in apks if module and f"/{module}/build/" in path.as_posix()]
    return (preferred or apks or [None])[0]


def build_current(target: Path, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """`CLI device build`: the current tree's APK, built in a build slot with no device held, so the
    Device stage only runs `adb install` while it holds its device. Kept in `RUN/after-build.json` per
    source fingerprint: the background build started with the gate is reused, and a second call for the
    same source waits for the first instead of building twice."""
    from android_workflow.base import _run as run_build, find_apks
    from android_workflow.cli import append_stage_log, read_json, source_fingerprint
    from android_workflow.locks import build_slot, exclusive

    if config is None:
        config_path = cache_dir(target) / "project-config.json"
        config = read_json(config_path) if config_path.is_file() else {}
    command = (config.get("commands") or {}).get("build")
    if not command:
        return {"status": "no_build_command", "reason": "Setup recorded no build command: the install task builds"}
    agent_dir = run_dir(target)
    record_path = agent_dir / AFTER_BUILD_FILE
    with exclusive(agent_dir / ".after-build.lock"):
        fingerprint = source_fingerprint(target)
        record = read_json(record_path) if record_path.is_file() else {}
        apk = Path(str(record.get("apk") or ""))
        if record.get("status") == "passed" and record.get("fingerprint") == fingerprint and apk.is_file() \
                and _digest(apk) == record.get("sha256"):
            return {**record, "cached": True}
        timeout = int((config.get("execution") or {}).get("command_timeout_seconds") or 900) * 2
        log = agent_dir / "device-build.log"
        started = time.time()
        with build_slot(f"device build ({target.name})", config) as slot:
            code = run_build(command, target, log, timeout)
        # An up-to-date build does not write the APK again: then the newest one is this source's.
        built = _pick_apk(find_apks(target, started), config) or (_pick_apk(find_apks(target, 0.0), config) if code == 0 else None)
        result: dict[str, Any] = {
            "status": "passed" if code == 0 and built else "no_apk" if code == 0 else "failed",
            "fingerprint": fingerprint, "command": command,
            "build_seconds": round(time.time() - started, 1), "slot_wait_seconds": slot.get("waited_seconds"),
            "log": str(log),
        }
        if built is not None and code == 0:
            result.update({"apk": str(built), "sha256": _digest(built)})
        elif code == 124:
            result["reason"] = f"the build timed out after {timeout}s"
        elif code:
            result["reason"] = f"the build failed (exit {code}): {_tail(log, 5)}"
        else:
            result["reason"] = "the build passed but wrote no APK under build/outputs/apk: the install task builds"
        if source_fingerprint(target) != fingerprint:
            result["note"] = "the source changed during the build: the next install builds again"
        record_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    append_stage_log(agent_dir, "T7", {"passed": "built", "no_apk": "built"}.get(result["status"], "build_failed"), "cli",
                     f"device APK in {result['build_seconds']}s" if result["status"] == "passed" else str(result.get("reason")))
    return result


def install_apk(adb: str, serial: str, apk: Path, log: Path, downgrade: bool = False) -> tuple[bool, str]:
    """`adb install` an APK already built: seconds, not a Gradle build, while the device is held."""
    try:
        with log.open("w", encoding="utf-8") as handle:
            result = subprocess.run([adb, "-s", serial, "install", "-r", *(["-d"] if downgrade else []), str(apk)],
                                    stdout=handle, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, timeout=600,
                                    check=False)
    except subprocess.TimeoutExpired:
        return False, "adb install timed out"
    except OSError as error:
        return False, f"could not run adb: {error}"
    if result.returncode != 0 or "Failure" in _tail(log, 5):
        return False, f"adb install failed: {_tail(log, 5)}"
    return True, str(apk)


def base_apk(target: Path, config: dict[str, Any]) -> Path | None:
    """The base APK the base build recorded for this run (`RUN/prebuild.json`), the app's own first."""
    from android_workflow.prebuild import current_status

    record = current_status(target)
    if record.get("status") != "passed":
        return None
    apks = [Path(str(item.get("path"))) for item in record.get("apks") or [] if Path(str(item.get("path"))).is_file()]
    if not apks:
        return None
    install = str((config.get("commands") or {}).get("install") or "")
    module = install.split(":")[1] if install.count(":") >= 2 else ""
    preferred = [path for path in apks if module and module in str(path)]
    return (preferred or apks)[0]


def install_base(adb: str, serial: str, apk: Path, log: Path) -> tuple[bool, str]:
    """`adb install` the cached base APK (`-d`: the base may carry a lower version code)."""
    installed, detail = install_apk(adb, serial, apk, log, downgrade=True)
    return installed, detail if installed else detail.replace("adb install failed", "the base install failed")


def _adb(target: Path | None = None) -> str:
    found = shutil.which("adb")
    if not found and target is not None:
        from android_workflow.emulator import adb_path

        found = adb_path(target)
    return found or str(Path.home() / "Library/Android/sdk/platform-tools/adb")


JANKY = re.compile(r"Janky frames:\s*(\d+)\s*\(([\d.]+)%\)")
TOTAL_FRAMES = re.compile(r"Total frames rendered:\s*(\d+)")
PERCENTILE = re.compile(r"^(\d+)th percentile:\s*(\d+)ms", re.M)


def _frame_stats(folder: Path, flow: str, target: Path, phase: str) -> dict[str, Any] | None:
    """A perf flow's `dumpsys gfxinfo` (`<name>-gfxinfo.txt` in its output): filed under
    `media/<phase>/` and summed up (frames, janky %, p50/p90/p99) for the before/after comparison."""
    found = sorted(folder.rglob("*gfxinfo*.txt"))
    if not found:
        return None
    text = found[0].read_text(encoding="utf-8", errors="replace")
    destination = media_dir(target) / phase / f"{flow}-gfxinfo.txt"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(found[0], destination)
    stats: dict[str, Any] = {"file": str(destination)}
    total, janky = TOTAL_FRAMES.search(text), JANKY.search(text)
    if total:
        stats["frames"] = int(total.group(1))
    if janky:
        stats["janky_frames"], stats["janky_percent"] = int(janky.group(1)), float(janky.group(2))
    for percentile, millis in PERCENTILE.findall(text):
        if percentile in {"50", "90", "99"}:
            stats[f"p{percentile}_ms"] = int(millis)
    return stats


def _event_count(folder: Path, flow: str) -> dict[str, Any] | None:
    """What a logcat flow (`<flow>.sh` from the scaffold) matched: the count and the filtered file."""
    path = folder / f"{flow}-events.txt"
    if not path.is_file():
        return None
    try:
        lines = [line for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
    except OSError:
        return None
    return {"matches": len(lines), "file": str(path)}


def install_current(target: Path, serial: str) -> dict[str, Any]:
    """`CLI device install`: the current build on `serial`. The APK is built first with no device held
    (`build_current`), then installed with `adb install` inside the device's lease, with its sha256 so
    the report can prove what it judged. The Device agent never runs Gradle."""
    from android_workflow.base import find_apks
    from android_workflow.cli import read_json
    from android_workflow.locks import device_lease

    config_path = cache_dir(target) / "project-config.json"
    config = read_json(config_path) if config_path.is_file() else {}
    log = run_dir(target) / "device-install.log"
    built = build_current(target, config)
    if built.get("status") not in INSTALLABLE:
        return {"status": "install_failed", "reason": built.get("reason") or "the build failed", "log": built.get("log"),
                "build_seconds": built.get("build_seconds")}
    with device_lease(serial, run_dir(target), timeout=1200) as held:
        if not held:
            return {"status": "device_busy", "serial": serial}
        started = time.time()
        if built.get("apk"):
            installed, detail = install_apk(_adb(target), serial, Path(str(built["apk"])), log)
        else:
            installed, detail = install(target, config, serial, log)
    if not installed:
        return {"status": "install_failed", "reason": detail, "log": str(log)}
    apk = Path(str(built["apk"])) if built.get("apk") else next(iter(find_apks(target, 0.0)), None)
    return {"status": "installed", "serial": serial, "command": detail, "seconds": round(time.time() - started, 1),
            "build_seconds": built.get("build_seconds", 0) if not built.get("cached") else 0,
            "package": (config.get("device") or {}).get("application_id"),
            **({"apk": str(apk), "sha256": built.get("sha256") or hashlib.sha256(apk.read_bytes()).hexdigest()} if apk else {})}


def _drop_phase(target: Path, phase: str, names: list[str]) -> None:
    from android_workflow.evidence import load_manifest, save_manifest

    folder = media_dir(target) / phase
    if not names or not folder.is_dir():
        return
    for path in folder.glob("*"):
        if path.is_file() and path.stem in names:
            path.unlink()
    manifest = load_manifest(target)
    manifest["items"] = [item for item in manifest.get("items") or []
                         if not (item.get("phase") == phase and item.get("name") in names)]
    save_manifest(target, manifest)


def _restore_network(serial: str) -> None:
    """A script that turned the network off must never leave it off."""
    adb = _adb()
    for service in ("wifi", "data"):
        try:
            subprocess.run([adb, "-s", serial, "shell", "svc", service, "enable"], capture_output=True,
                           stdin=subprocess.DEVNULL, timeout=15, check=False)
        except (OSError, subprocess.SubprocessError):
            pass


def _run_flow(flow: Path, maestro: str, serial: str, folder: Path, target: Path, phase: str,
              timeout: int) -> tuple[int, str]:
    """One attempt: a Maestro flow, or the `.sh` that drives it. Returns (exit code, log path)."""
    log = folder / "maestro.out"
    if flow.suffix == ".sh":
        sibling = next((flow.with_suffix(ext) for ext in (".yaml", ".yml") if flow.with_suffix(ext).is_file()), None)
        env = {**os.environ, "ANDROID_SERIAL": serial, "AW_SERIAL": serial, "AW_MAESTRO": maestro, "AW_OUT": str(folder),
               "AW_FLOW": str(sibling or ""), "AW_PHASE": phase, "AW_TARGET": str(target)}
        command = [str(flow)] if os.access(flow, os.X_OK) else ["sh", str(flow)]
    else:
        env = None
        command = [maestro, "--device", serial, "test", "--test-output-dir", str(folder), str(flow)]
    try:
        with log.open("w", encoding="utf-8") as handle:
            run = subprocess.run(command, cwd=target, env=env, stdout=handle, stderr=subprocess.STDOUT,
                                 stdin=subprocess.DEVNULL, timeout=timeout, check=False)
        code = run.returncode
    except subprocess.TimeoutExpired:
        code = 124
    except OSError as error:
        log.write_text(f"could not run the flow: {error}\n", encoding="utf-8")
        code = 126
    if flow.suffix == ".sh":
        _restore_network(serial)
    return code, str(log)


def replay(target: Path, serial: str | None = None, phase: str = "after", install_base_apk: bool | None = None,
           lease_timeout: float = 1200.0) -> dict[str, Any]:
    """Run every saved flow and file what it captured under `media/<phase>/`, holding the device's
    lease the whole time (another run's Device stage waits, and so does this replay for theirs).

    `after` installs the current build first. `before` installs the cached base APK when the base
    build has one (`install_base_apk`, default: when no `before` agent installed it already, i.e. the
    late `before` after the `after` round), else runs on the build already installed. Returns what was
    captured per flow, with the time each step took."""
    from android_workflow.emulator import status as device_status
    from android_workflow.locks import device_lease

    if phase not in {"before", "after"}:
        return {"status": "failed", "reason": "phase must be before or after"}
    if not flows(target):
        return {"status": "no_flows", "next": "drive the app by hand (device-driving skill) and save the flow in media/flows/"}
    if not serial:
        current = device_status(target)
        if current.get("status") not in {"connected", "ready"}:
            return {"status": "no_device", "reason": current.get("reason") or current.get("status")}
        serial = str(current.get("serial"))
    built: dict[str, Any] = {}
    if phase == "after":  # the build runs before the device is held: other runs keep using theirs meanwhile
        built = build_current(target)
        if built.get("status") not in INSTALLABLE:
            result = {"status": "install_failed", "phase": phase, "reason": built.get("reason") or "the build failed",
                      "log": built.get("log"), "build_seconds": built.get("build_seconds")}
            (run_dir(target) / REPLAY_FILE).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
            return result
    with device_lease(serial, run_dir(target), timeout=lease_timeout) as held:
        if not held:
            return {"status": "device_busy", "serial": serial,
                    "reason": f"another run kept {serial} for over {int(lease_timeout)}s"}
        return _replay(target, serial, phase, install_base_apk, built)


def _replay(target: Path, serial: str, phase: str, install_base_apk: bool | None,
            built: dict[str, Any] | None = None) -> dict[str, Any]:
    from android_workflow.cli import append_stage_log, read_json, source_fingerprint
    from android_workflow.evidence import compare, ingest

    agent_dir = run_dir(target)
    saved = flows(target)
    maestro = maestro_path()
    if not maestro:
        return {"status": "no_maestro", "next": "install Maestro (device-driving §9), then replay again"}
    config_path = cache_dir(target) / "project-config.json"
    config = read_json(config_path) if config_path.is_file() else {}
    device_config = config.get("device") or {}
    flow_timeout = int(device_config.get("flow_timeout_seconds") or FLOW_TIMEOUT_SECONDS)
    budget = int(device_config.get("after_budget_seconds") or AFTER_BUDGET_SECONDS)
    out = agent_dir / "media" / f"replay-{phase}"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    started = time.monotonic()
    install_seconds = 0.0
    result_file = agent_dir / (REPLAY_FILE if phase == "after" else f"replay-{phase}.json")
    explicit = install_base_apk is True
    if phase == "before" and install_base_apk is None:
        install_base_apk = True  # the cached base when there is one, else the build already installed
    base = base_apk(target, config) if phase == "before" and install_base_apk else None
    # Without a cached base, the installed build is the base only while no `after` build has been
    # installed yet (the `before` agent's own proof). After an `after` round it is the new build.
    device_rounds = ((read_json(agent_dir / "run-state.json").get("stages") or {}).get("T7") or {}).get("history") \
        if (agent_dir / "run-state.json").is_file() else None
    after_installed = (agent_dir / REPLAY_FILE).is_file() or bool(device_rounds)
    if phase == "before" and base is None and (explicit or after_installed):
        result = {"status": "no_base_apk", "reason": "the base build has no APK yet (`CLI prebuild --target TARGET --wait`)"}
        result_file.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        return result
    if phase == "after" or base is not None:
        installing = time.monotonic()
        if base is not None:
            installed, detail = install_base(_adb(target), serial, base, out / "install.log")
        elif (built or {}).get("apk"):
            installed, detail = install_apk(_adb(target), serial, Path(str(built["apk"])), out / "install.log")
        else:
            installed, detail = install(target, config, serial, out / "install.log")
        install_seconds = round(time.monotonic() - installing, 1)
        if not installed:
            result = {"status": "install_failed", "reason": detail, "log": str(out / "install.log"),
                      "install_seconds": install_seconds}
            result_file.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
            return result
    before, after = media_dir(target) / "before", media_dir(target) / "after"
    declared = {flow.name: declared_names(flow) for flow in saved}
    # What a flow may file: the names it declares, plus (older flows) the names the before capture used.
    known = sorted({path.stem for path in before.glob("*") if path.is_file()}) if before.is_dir() else []
    owned = sorted({name for names in declared.values() for name in names} | (set(known) if phase == "after" else set()))
    _drop_phase(target, phase, owned)  # what this replay must capture again never stays from an earlier round
    results = []
    for flow in saved:
        folder = out / flow.stem
        folder.mkdir(parents=True, exist_ok=True)
        flow_started = time.monotonic()
        code, log = _run_flow(flow, maestro, serial, folder, target, phase, flow_timeout)
        attempts = 1
        if code != 0:  # a real failure is rerun once; a second one is a failure, the first alone was flaky
            code, log = _run_flow(flow, maestro, serial, folder, target, phase, flow_timeout)
            attempts = 2
        allowed = set(declared[flow.name]) | (set(known) if phase == "after" else set())
        captured = []
        # A failed flow's files are failure screenshots, never evidence. On the base build a flow that
        # looks for the new element fails by design: what it captured before that step is the before.
        if code == 0 or (phase == "before" and allowed):
            for media in sorted([*folder.rglob("*.png"), *folder.rglob("*.mp4")]):
                if allowed and media.stem not in allowed:
                    continue  # only the names this flow declares (or the before capture used)
                item = ingest(target, phase, media.stem, media)
                captured.append(item["name"])
        metrics = _frame_stats(folder, flow.stem, target, phase)
        events = _event_count(folder, flow.stem)
        status = "passed" if code == 0 and (captured or metrics or events or phase == "before" and not allowed) else \
            "partial" if phase == "before" and captured else "failed"
        results.append({"flow": flow.name, "status": status, "exit_code": code, "captured": captured,
                        "attempts": attempts, "seconds": round(time.monotonic() - flow_started, 1),
                        **({"frames": metrics} if metrics else {}),
                        **({"events": events} if events else {}),
                        **({"log_tail": _tail(Path(log))} if code else {}),
                        **({"reason": "the flow captured nothing it declares"} if code == 0 and not captured and status == "failed" else {})})
    expected_after = owned if phase == "after" else sorted({name for names in declared.values() for name in names})
    target_dir = after if phase == "after" else before
    identical = sorted(
        path.stem for path in after.glob("*.png")
        if (before / path.name).is_file() and _digest(before / path.name) == _digest(path)
    ) if phase == "after" and after.is_dir() else []
    missing = sorted(name for name in expected_after if not (target_dir.is_dir() and any(target_dir.glob(name + ".*"))))
    passed = sum(item["status"] == "passed" for item in results)
    if phase == "before":  # before evidence is whatever the base build could show
        passed = sum(item["status"] in {"passed", "partial"} for item in results)
    status = "replayed" if passed == len(results) else "partial" if passed else "failed"
    total = round(time.monotonic() - started, 1)
    build_seconds = 0.0 if (built or {}).get("cached") else float((built or {}).get("build_seconds") or 0)
    flows_seconds = round(total - install_seconds, 1)
    result = {
        "status": status, "phase": phase, "serial": serial, "fingerprint": source_fingerprint(target),
        "flows": results, "identical_to_before": identical, "missing_from_after" if phase == "after" else "missing": missing,
        "build_seconds": build_seconds, "install_seconds": install_seconds, "flows_seconds": flows_seconds,
        "duration_seconds": total, "budget_seconds": budget,
        **({"base_apk": str(base)} if base is not None else {}),
        **({"apk": built["apk"], "sha256": built.get("sha256")} if (built or {}).get("apk") and base is None else {}),
        # The budget is the agent's time on the device: the build ran before the device was held and the
        # install is the CLI's, so neither counts against it.
        "over_budget": phase == "after" and flows_seconds > budget,
        "next": {
            "replayed": "judge each device AC from media/after (and compare.md); drive by hand only what no flow covers"
                        if phase == "after" else "the flows run on the base build: leave them in media/flows/ for the after replay",
            "partial": "the failed flows need driving by hand; the rest are captured in media/" + phase,
            "failed": "no flow ran: drive the app by hand and fix the flows",
        }[status],
    }
    compare(target)
    if phase == "after" and status == "replayed":
        from android_workflow.flows import remember

        result["library"] = remember(target)  # the next ticket on this screen copies these flows
    result_file.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    note = f"{passed}/{len(results)} flow(s) {'replayed on the new build' if phase == 'after' else 'run on the base build'}" \
           f" in {flows_seconds}s" + (f" (build {build_seconds}s before the device, install {install_seconds}s)"
                                      if phase == "after" else "")
    append_stage_log(agent_dir, "T7", "replayed" if phase == "after" else "flows_ready", "cli", note)
    return result
