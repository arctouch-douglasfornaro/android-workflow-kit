"""Replay the Device agent's Maestro flows on the current build.

The `before` capture already found the way to the changed screen and saved it as Maestro flows in
`RUN/media/flows/`. For `after`, the same flows are run again by code: install the current build,
run each flow on the chosen device, and file the screenshots and clips under `media/after/` with
the names they had before. The Device agent then judges the acceptance criteria from those files
instead of driving the app again; a flow that fails sends it back to driving by hand.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from android_workflow.paths import cache_dir, media_dir, run_dir

REPLAY_FILE = "replay.json"
FLOW_TIMEOUT_SECONDS = 300


def maestro_path() -> str | None:
    found = shutil.which("maestro")
    if found:
        return found
    default = Path.home() / ".maestro" / "bin" / "maestro"
    return str(default) if default.is_file() and os.access(default, os.X_OK) else None


def flows(target: Path) -> list[Path]:
    folder = media_dir(target) / "flows"
    return sorted(folder.glob("*.yaml")) + sorted(folder.glob("*.yml")) if folder.is_dir() else []


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


def _drop_after(target: Path, names: list[str]) -> None:
    from android_workflow.evidence import load_manifest, save_manifest

    after = media_dir(target) / "after"
    if not names or not after.is_dir():
        return
    for path in after.glob("*"):
        if path.is_file() and path.stem in names:
            path.unlink()
    manifest = load_manifest(target)
    manifest["items"] = [item for item in manifest.get("items") or []
                         if not (item.get("phase") == "after" and item.get("name") in names)]
    save_manifest(target, manifest)


def replay(target: Path, serial: str | None = None) -> dict[str, Any]:
    """Install the current build and run every saved flow; returns what was captured per flow."""
    from android_workflow.cli import append_stage_log, read_json, source_fingerprint
    from android_workflow.emulator import status as device_status
    from android_workflow.evidence import ingest

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
    out = agent_dir / "media" / "replay"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    started = time.monotonic()
    installed, detail = install(target, config, serial, out / "install.log")
    if not installed:
        result = {"status": "install_failed", "reason": detail, "log": str(out / "install.log")}
        (agent_dir / REPLAY_FILE).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        return result
    before, after = media_dir(target) / "before", media_dir(target) / "after"
    expected = sorted({path.stem for path in before.glob("*") if path.is_file()}) if before.is_dir() else []
    _drop_after(target, expected)  # what this replay must capture again never stays from an earlier round
    results = []
    for flow in saved:
        folder = out / flow.stem
        folder.mkdir(parents=True, exist_ok=True)
        log = folder / "maestro.out"
        try:
            with log.open("w", encoding="utf-8") as handle:
                run = subprocess.run([maestro, "--device", serial, "test", "--test-output-dir", str(folder), str(flow)],
                                     cwd=target, stdout=handle, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                     timeout=FLOW_TIMEOUT_SECONDS, check=False)
            code = run.returncode
        except subprocess.TimeoutExpired:
            code = 124
        captured = []
        if code == 0:  # a failed flow's files are failure screenshots, never evidence
            for media in sorted([*folder.rglob("*.png"), *folder.rglob("*.mp4")]):
                if expected and media.stem not in expected:
                    continue  # only the names the before capture used
                item = ingest(target, "after", media.stem, media)
                captured.append(item["name"])
        status = "passed" if code == 0 and captured else "failed"
        results.append({"flow": flow.name, "status": status, "exit_code": code, "captured": captured,
                        **({"log_tail": _tail(log)} if code else {}),
                        **({"reason": "the flow captured nothing with a before name"} if code == 0 and not captured else {})})
    identical = sorted(
        path.stem for path in after.glob("*.png")
        if (before / path.name).is_file() and _digest(before / path.name) == _digest(path)
    ) if after.is_dir() else []
    missing = sorted(name for name in expected if not (after.is_dir() and any(after.glob(name + ".*"))))
    passed = sum(item["status"] == "passed" for item in results)
    status = "replayed" if passed == len(results) else "partial" if passed else "failed"
    result = {
        "status": status, "serial": serial, "fingerprint": source_fingerprint(target),
        "flows": results, "identical_to_before": identical, "missing_from_after": missing,
        "duration_seconds": round(time.monotonic() - started, 1),
        "next": {
            "replayed": "judge each device AC from media/after (and compare.md); drive by hand only what no flow covers",
            "partial": "the failed flows need driving by hand; the rest are captured in media/after",
            "failed": "no flow ran on the new build: drive the app by hand and fix the flows",
        }[status],
    }
    from android_workflow.evidence import compare

    compare(target)
    (agent_dir / REPLAY_FILE).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    append_stage_log(agent_dir, "T7", "replayed", "cli", f"{passed}/{len(results)} flow(s) replayed on the new build")
    return result
