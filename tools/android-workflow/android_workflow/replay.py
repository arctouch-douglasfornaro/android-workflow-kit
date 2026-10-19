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
    command = (config.get("commands") or {}).get("install")
    if not command:
        return False, "no install command recorded by Setup"
    timeout = int((config.get("execution") or {}).get("command_timeout_seconds") or 900) * 2
    env = {**os.environ, "ANDROID_SERIAL": serial}
    try:
        with log.open("w", encoding="utf-8") as handle:
            result = subprocess.run(shlex.split(command) + ["--console=plain"], cwd=target, env=env, stdout=handle,
                                    stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return False, f"the install timed out after {timeout}s"
    except OSError as error:
        return False, f"could not run the install: {error}"
    if result.returncode != 0:
        return False, f"the install failed (exit {result.returncode}): {_tail(log, 5)}"
    return True, command


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
    adb = shutil.which("adb") or str(Path.home() / "Library/Android/sdk/platform-tools/adb")
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


def replay(target: Path, serial: str | None = None, phase: str = "after") -> dict[str, Any]:
    """Run every saved flow and file what it captured under `media/<phase>/`.

    `after` installs the current build first; `before` runs on the base build the Device agent already
    installed. Returns what was captured per flow, with the time each step took."""
    from android_workflow.cli import append_stage_log, read_json, source_fingerprint
    from android_workflow.emulator import status as device_status
    from android_workflow.evidence import compare, ingest

    if phase not in {"before", "after"}:
        return {"status": "failed", "reason": "phase must be before or after"}
    agent_dir = run_dir(target)
    saved = flows(target)
    if not saved:
        return {"status": "no_flows", "next": "drive the app by hand (device-driving skill) and save the flow in media/flows/"}
    maestro = maestro_path()
    if not maestro:
        return {"status": "no_maestro", "next": "install Maestro (device-driving §9), then replay again"}
    if not serial:
        current = device_status(target)
        if current.get("status") not in {"connected", "ready"}:
            return {"status": "no_device", "reason": current.get("reason") or current.get("status")}
        serial = str(current.get("serial"))
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
    if phase == "after":
        installing = time.monotonic()
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
        if code == 0:  # a failed flow's files are failure screenshots, never evidence
            for media in sorted([*folder.rglob("*.png"), *folder.rglob("*.mp4")]):
                if allowed and media.stem not in allowed:
                    continue  # only the names this flow declares (or the before capture used)
                item = ingest(target, phase, media.stem, media)
                captured.append(item["name"])
        status = "passed" if code == 0 and (captured or phase == "before" and not allowed) else "failed"
        results.append({"flow": flow.name, "status": status, "exit_code": code, "captured": captured,
                        "attempts": attempts, "seconds": round(time.monotonic() - flow_started, 1),
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
    status = "replayed" if passed == len(results) else "partial" if passed else "failed"
    total = round(time.monotonic() - started, 1)
    result = {
        "status": status, "phase": phase, "serial": serial, "fingerprint": source_fingerprint(target),
        "flows": results, "identical_to_before": identical, "missing_from_after" if phase == "after" else "missing": missing,
        "install_seconds": install_seconds, "duration_seconds": total, "budget_seconds": budget,
        "over_budget": phase == "after" and total > budget,
        "next": {
            "replayed": "judge each device AC from media/after (and compare.md); drive by hand only what no flow covers"
                        if phase == "after" else "the flows run on the base build: leave them in media/flows/ for the after replay",
            "partial": "the failed flows need driving by hand; the rest are captured in media/" + phase,
            "failed": "no flow ran: drive the app by hand and fix the flows",
        }[status],
    }
    compare(target)
    result_file.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    note = f"{passed}/{len(results)} flow(s) {'replayed on the new build' if phase == 'after' else 'run on the base build'}" \
           f" in {total}s" + (f" (install {install_seconds}s)" if phase == "after" else "")
    append_stage_log(agent_dir, "T7", "replayed" if phase == "after" else "flows_ready", "cli", note)
    return result
