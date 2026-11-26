"""Run the Device agent's saved Maestro flows by code, on either build.

The Device `before` agent finds the way to the changed screen once, while the Implementer works, and
saves it as flows in `RUN/media/flows/`. Each flow names its own captures (`takeScreenshot: <name>`,
`startRecording: <name>`): that name is the evidence name, so the same flow files its output under
`media/before/` on the base build and under `media/after/` on the new one. The `after` Device agent
then only replays and judges; a flow that fails sends it back to driving by hand.

Flow files: `*.yaml` run with Maestro; `_*.yaml` are helpers a flow pulls in with `runFlow` and never
run alone; a `<flow>.sh` next to `<flow>.yaml` runs instead of it, for what Maestro cannot do (turn
the network off at a marker) and gets `AW_SERIAL`, `AW_MAESTRO`, `AW_OUT`, `AW_FLOW`, `AW_PHASE` and
`AW_REPLAY_STATE`: a folder emptied at the start of each replay, where a script marks setup that later
flows of the same replay can skip (a feature flag already set, `adb root` already done).

The `.yaml` flows a replay runs take their first attempt in ONE `maestro test` call (Maestro's 10-15 s
startup paid once), each filed as if it ran alone; a `.sh` runs alone, after that call, and so does a
retry or a fix round (which stops at its first failure). A batched flow must start from its own
`launchApp`, never from where the previous flow left the app. Maestro gets `-e AW_PHASE=<phase>`.

A proof with nothing to see (an analytics event, audio, a log line) is a comment in the flow's own yaml,
so the flow still batches: `# aw-events: <regex on "<tag>: <message>">`, and optionally
`# aw-events-tag: <logcat tag>`, `# aw-events-count: N | N.. | N..M` (default `1..`, checked on the
`after` build only) and `# aw-events-fields: a b` (keep only those JSON fields of each line). The CLI
reads logcat while the flows run, keeps only the matching lines (never the full log: user ids, tokens),
gives each flow the lines of its own time window in the call (JUnit's per-flow start) and writes them
to `<flow>-events.txt`; a count off fails the flow with cause `events`.
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

from android_workflow import gradle_cache
from android_workflow.paths import cache_dir, media_dir, run_dir

REPLAY_FILE = "replay.json"
AFTER_BUILD_FILE = "after-build.json"
INSTALLABLE = frozenset({"passed", "no_build_command", "no_apk"})  # no APK to `adb install`: the install task builds
FLOW_TIMEOUT_SECONDS = 180
AFTER_BUDGET_SECONDS = 300
STATE_DIR = "_state"  # `AW_REPLAY_STATE`: emptied at each replay; scripts mark one-time setup done there
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


HELPER_REF_RE = re.compile(r"(?<![\w.-])(_[\w.-]+\.ya?ml)\b")


def _own_files(flow: Path) -> list[Path]:
    return [flow, *(flow.with_suffix(ext) for ext in (".yaml", ".yml"))] if flow.suffix == ".sh" else [flow]


def helpers(flow: Path) -> list[Path]:
    """The `_helpers` a flow pulls in (`runFlow: _nav.yaml`, or named in its `.sh`), and the ones those
    pull in. An edit to a helper reruns only the flows that use it."""
    found: dict[str, Path] = {}
    todo = [path for path in _own_files(flow) if path.is_file()]
    while todo:
        try:
            text = todo.pop().read_text(encoding="utf-8")
        except OSError:
            continue
        for name in HELPER_REF_RE.findall(text):
            helper = flow.parent / name
            if name not in found and helper.is_file():
                found[name] = helper
                todo.append(helper)
    return sorted(found.values())


def flow_digest(flow: Path) -> str:
    """What a flow runs: its own files and the `_helpers` it uses (a fix often lands in a helper)."""
    sha = hashlib.sha256()
    for path in [*_own_files(flow), *helpers(flow)]:
        if path.is_file():
            sha.update(path.name.encode() + b"\0" + path.read_bytes())
    return sha.hexdigest()[:16]


STALE_FAILS = 2  # an unchanged flow that failed this many replays on an unchanged source is not run again
UNSTABLE_FLAKES = 2  # an unchanged flow flaky in this many replays is no longer run: its AC is checked by hand
FLOW_HEALTH_FILE = "flow-health.json"  # RUN/: how often each flow (per digest) was flaky, across sources
DEVICE_ERRORS_IN_A_ROW = 2  # this many flows in a row failing on device errors stop the replay
MAESTRO_DRIVER_PACKAGES = ("dev.mobile.maestro", "dev.mobile.maestro.test")
# The device or Maestro failed, not the app: gRPC statuses of Maestro's driver and adb's own errors.
DEVICE_ERROR_RE = re.compile(r"\bUNAVAILABLE\b|DEADLINE_EXCEEDED|DeviceServerDied|Device server died|io\.grpc\.|device offline|device '[^']*' not found|"
                             r"no devices/emulators found|Failed to connect to|Connection refused|ECONNRESET|"
                             r"Maestro driver|driver (?:did not|failed to) start|Timed out waiting for (?:the )?driver")
APP_CRASH_RE = re.compile(r"App crashed|FATAL EXCEPTION|keeps stopping|isn't responding")
SELECTOR_RE = re.compile(r"^\s*-?\s*(?:id|text|tapOn|assertVisible|assertNotVisible|doubleTapOn|longPressOn|"
                         r"scrollUntilVisible|copyTextFrom)\s*:\s*['\"]?([^'\"\n#{}]+?)['\"]?\s*(?:#.*)?$", re.M)


def failure_cause(code: int, log_text: str) -> str:
    """Why a flow failed: `app_crash` (the app died), `device` (Maestro's driver or adb, not the app),
    `timeout` (the flow ran past its limit) or `step` (a step found the screen not as the flow expects)."""
    if APP_CRASH_RE.search(log_text):
        return "app_crash"
    if code == 126 or DEVICE_ERROR_RE.search(log_text):
        return "device"
    return "timeout" if code == 124 else "step"


MAESTRO_LOGS = Path.home() / ".maestro" / "tests"


def failure_text(log: Path, folder: Path, serial: str, since: float) -> str:
    """What explains a failed attempt: its output, plus Maestro's own `maestro.log` (under the output
    folder, or `~/.maestro/tests/<time>/` where Maestro writes it by default, the one of this device
    written since the attempt started). The driver's errors are only there, never in the output."""
    parts = [_tail(log, 200)]
    found = [path for path in folder.rglob("maestro.log") if path.stat().st_mtime >= since]
    try:
        found += [path for path in MAESTRO_LOGS.glob("*/maestro.log") if path.stat().st_mtime >= since]
    except OSError:
        pass
    for path in found[-3:]:
        text = _tail(path, 400)
        if serial in text or path.is_relative_to(folder):
            parts.append(text)
    return "\n".join(parts)


def selectors(flow: Path) -> set[str]:
    """The ids and texts a flow's own files look for (not its shared helpers): what ties it to the code."""
    found: set[str] = set()
    for path in _own_files(flow):
        try:
            text = path.read_text(encoding="utf-8") if path.is_file() and path.suffix != ".sh" else ""
        except OSError:
            continue
        for raw in SELECTOR_RE.findall(text):
            value = raw.strip().rsplit("/", 1)[-1]  # `com.app:id/save` -> `save`
            for piece in re.split(r"[.*+?^$()\[\]|\\]+", value):
                if len(piece.strip()) >= 3:
                    found.add(piece.strip())
    return found


def changed_text(target: Path, since_tree: str | None, now_tree: str | None) -> str | None:
    """The lines added or removed between two snapshots of the tree (`git diff -U0`), or None."""
    if not since_tree or not now_tree or since_tree == now_tree:
        return "" if since_tree and since_tree == now_tree else None
    try:
        diff = subprocess.run(["git", "-C", str(target), "diff", "-U0", "--no-color", since_tree, now_tree],
                              text=True, capture_output=True, check=False, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    if diff.returncode != 0:
        return None
    return "\n".join(line for line in diff.stdout.splitlines()
                     if line.startswith(("+", "-")) and not line.startswith(("+++", "---")) or line.startswith("diff --git"))


def affected(saved: list[Path], diff: str | None) -> list[str]:
    """The flows whose ids or texts the change touched (they show the changed screen): run first."""
    if not diff:
        return []
    return [flow.name for flow in saved if any(selector in diff for selector in selectors(flow))]


def _reset_device(serial: str, app: str | None) -> None:
    """Before a retry: stop the app and Maestro's driver, so the second attempt starts clean (a stuck
    driver fails every step the same way)."""
    adb = _adb()
    for package in ([app] if app else []) + list(MAESTRO_DRIVER_PACKAGES):
        try:
            subprocess.run([adb, "-s", serial, "shell", "am", "force-stop", package], capture_output=True,
                           stdin=subprocess.DEVNULL, timeout=15, check=False)
        except (OSError, subprocess.SubprocessError):
            pass
OPEN_LINK_RE = re.compile(r"^\s*-\s*openLink\b", re.M)
LAUNCH_APP_RE = re.compile(r"^\s*-\s*launchApp\b", re.M)


TOP_STEP_RE = re.compile(r"^-\s*([A-Za-z]+)\s*:?\s*(.*?)\s*$")
RUN_FLOW_FILE_RE = re.compile(r"^(?:file\s*:\s*)?['\"]?([\w./-]+\.ya?ml)['\"]?$")


def flow_steps(flow: Path, depth: int = 0) -> list[str]:
    """The flow's top-level commands in order, with each `runFlow: <helper>.yaml` inlined."""
    source = flow if flow.suffix != ".sh" else next((flow.with_suffix(ext) for ext in (".yaml", ".yml")
                                                    if flow.with_suffix(ext).is_file()), None)
    try:
        lines = source.read_text(encoding="utf-8").split("\n---", 1)[-1].splitlines() if source else []
    except OSError:
        return []
    steps: list[str] = []
    for index, line in enumerate(lines):
        step = TOP_STEP_RE.match(line)
        if not step:
            continue
        name, rest = step.group(1), step.group(2)
        if name == "runFlow" and depth < 5:
            nested = rest or next((raw.strip() for raw in lines[index + 1:index + 3] if "file" in raw), "")
            helper = RUN_FLOW_FILE_RE.match(nested.strip())
            if helper and (flow.parent / helper.group(1)).is_file():
                steps += flow_steps(flow.parent / helper.group(1), depth + 1)
                continue
        steps.append(name)
    return steps


def launch_then_link(flow: Path) -> str | None:
    """`launchApp` straight into `openLink`: the app's first screen and the link's activity start
    together, and on a slow device the link's screen is not drawn yet when the flow captures it (the
    capture shows the previous app while the asserts already passed)."""
    steps = flow_steps(flow)
    if any(name == "launchApp" and following == "openLink" for name, following in zip(steps, steps[1:])):
        return (f"{flow.name}: openLink right after launchApp: wait on the app's first screen in between "
                "(`extendedWaitUntil` on a test tag or text it shows), or the link's screen may not be drawn "
                "when the flow captures it")
    return None


def flow_hints(flow: Path, log_text: str) -> list[str]:
    """Known causes of a failed flow that the flow text or its log already show."""
    hints = []
    for path in [flow.with_suffix(".yaml"), flow.with_suffix(".yml"), *helpers(flow)]:
        try:
            text = path.read_text(encoding="utf-8") if path.is_file() else ""
        except OSError:
            continue
        link = OPEN_LINK_RE.search(text)
        launch = LAUNCH_APP_RE.search(text, link.end()) if link else None
        if launch:
            hints.append(f"{path.name}: launchApp after openLink restarts the app and drops the deep link")
    if "err=-38" in log_text or "Encoder failed" in log_text:
        hints.append("this device cannot record video (encoder -38): capture with takeScreenshot only")
    return hints


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
            parts, env = gradle_cache.wrap(shlex.split(command) + ["--console=plain"], target, env)
            result = subprocess.run(parts, cwd=target, env=env, stdout=handle,
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
        from android_workflow.cli import worktree_build_lock

        # After the gate's build in this checkout, never at the same time (Gradle's lock timeouts).
        with worktree_build_lock(target), build_slot(f"device build ({target.name})", config) as slot:
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


EVENTS_RE = re.compile(r"^\s*#\s*aw-events(?:-(tag|count|fields))?\s*:[ \t]*(.*?)\s*$", re.M)
LOGCAT_LINE = re.compile(r"^\s*(\d+\.\d+)\s+\d+\s+\d+\s+[VDIWEFA]\s+(.*?)\s*: (.*)$")
EVENT_WINDOW_SLACK = 1.0  # JUnit starts are whole seconds; a flow's own events come after its launchApp
EVENT_DRAIN_SECONDS = 1.0  # logcat lines still in flight when Maestro exits


def event_check(flow: Path) -> dict[str, Any] | None:
    """The logcat proof a Maestro flow declares in its own yaml (`# aw-events: ...`), or None."""
    if flow.suffix == ".sh":
        return None
    try:
        text = flow.read_text(encoding="utf-8")
    except OSError:
        return None
    found = {key or "pattern": value for key, value in EVENTS_RE.findall(text)}
    pattern = found.get("pattern", "")
    if not pattern:
        return None
    check: dict[str, Any] = {"pattern": pattern, "tag": found.get("tag") or None,
                             "fields": (found.get("fields") or "").split()}
    if "TODO" in pattern:
        return {**check, "error": f"fill `# aw-events:` in {flow.name}: the pattern still has a TODO"}
    try:
        check["regex"] = re.compile(pattern)
    except re.error as error:
        return {**check, "error": f"`# aw-events:` in {flow.name} is not a valid regex: {error}"}
    count = re.fullmatch(r"(\d+)\s*(\.\.)?\s*(\d*)", found.get("count") or "1..")
    if not count:
        return {**check, "error": f"`# aw-events-count:` in {flow.name} must be N, N.. or N..M"}
    low = int(count.group(1))
    check["min"], check["max"] = low, int(count.group(3)) if count.group(3) else None if count.group(2) else low
    return check


def _matches(check: dict[str, Any], tag: str, message: str) -> bool:
    return "regex" in check and (not check["tag"] or check["tag"] == tag) and bool(check["regex"].search(f"{tag}: {message}"))


class LogcatTap:
    """Logcat read while flows run, keeping only the lines some flow's `aw-events` matches, in memory,
    with the host time each was logged at (the device clock's offset measured once)."""

    def __init__(self, serial: str, checks: list[dict[str, Any]]):
        self.serial, self.checks = serial, [check for check in checks if "regex" in check]
        self.lines: list[tuple[float, str, str]] = []
        self.offset = 0.0
        self.error: str | None = None
        self.process: subprocess.Popen[str] | None = None
        self.thread: Any = None
        self.started = time.time()

    def start(self) -> "LogcatTap":
        import threading

        adb = _adb()
        self.started = time.time()
        try:
            before = time.time()
            now = subprocess.run([adb, "-s", self.serial, "shell", "date +%s.%N"], text=True, capture_output=True,
                                 stdin=subprocess.DEVNULL, timeout=15, check=False).stdout.strip()
            after = time.time()
            device_now = float(now) if re.fullmatch(r"\d+(\.\d+)?", now) else None
            if device_now is not None and "." in now:
                self.offset = device_now - (before + after) / 2
            since = ["-T", f"{(device_now or before) - 2:.3f}"] if device_now is not None else []
            tags = {check["tag"] for check in self.checks}
            spec = ["*:S", *(f"{tag}:V" for tag in sorted(tags))] if tags and None not in tags else []
            self.process = subprocess.Popen([adb, "-s", self.serial, "logcat", "-v", "epoch", *since, *spec],
                                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                            stdin=subprocess.DEVNULL, text=True, errors="replace",
                                            start_new_session=True)
        except (OSError, subprocess.SubprocessError, ValueError) as error:
            self.error = f"could not read logcat: {error}"
            return self
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()
        return self

    def _read(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        for line in self.process.stdout:
            parsed = LOGCAT_LINE.match(line)
            if parsed and any(_matches(check, parsed.group(2), parsed.group(3)) for check in self.checks):
                self.lines.append((float(parsed.group(1)) - self.offset, parsed.group(2), parsed.group(3)))

    def stop(self) -> None:
        if self.process is None:
            return
        time.sleep(EVENT_DRAIN_SECONDS)
        try:
            os.killpg(self.process.pid, 15)
        except (OSError, ProcessLookupError):
            self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
        if self.thread is not None:
            self.thread.join(timeout=5)
        if self.process.stdout is not None:
            self.process.stdout.close()


def _render_event(check: dict[str, Any], tag: str, message: str) -> str:
    if not check["fields"]:
        return f"{tag}: {message}"
    found = []
    for field in check["fields"]:
        value = re.search(rf'"{re.escape(field)}"\s*:\s*("[^"]*"|\[[^\]]*\]|[\w.+-]+)', message)
        if value:
            found.append(f"{field}={value.group(1)}")
    return " ".join(found)


def _finish_events(check: dict[str, Any] | None, tap: "LogcatTap | None", window: tuple[float, float] | None,
                   folder: Path, flow: Path, phase: str, code: int, log: str) -> tuple[int, dict[str, Any] | None, bool]:
    """A flow's `aw-events`: its lines in `<flow>-events.txt` and the count against what it expects.
    Returns (exit code, events for replay.json, whether the count failed the flow)."""
    if check is None:
        return code, None, False
    problem = check.get("error") or (tap.error if tap is not None else "no logcat was read")
    lines: list[str] = []
    if not problem and tap is not None:
        lines = [_render_event(check, tag, message) for at, tag, message in tap.lines
                 if (window is None or window[0] <= at < window[1]) and _matches(check, tag, message)]
    path = folder / f"{flow.stem}-events.txt"
    folder.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    expected = f"{check['min']}..{'' if check['max'] is None else check['max']}" if "min" in check else ""
    events: dict[str, Any] = {"matches": len(lines), "file": str(path), "pattern": check["pattern"],
                              **({"tag": check["tag"]} if check["tag"] else {}), **({"expected": expected} if expected else {})}
    off = phase == "after" and "min" in check and (len(lines) < check["min"] or (check["max"] is not None and len(lines) > check["max"]))
    if problem:
        events["error"] = problem
    note = problem or f"events matching {check['pattern']!r}: {len(lines)} (expected {expected or 'any'})"
    with open(log, "a", encoding="utf-8") as handle:
        handle.write(note + "\n")
    failed = code == 0 and bool(problem or off)
    return (5 if problem else 3) if failed else code, events, failed


def install_current(target: Path, serial: str) -> dict[str, Any]:
    """`CLI device install`: the current build on `serial`. The APK is built first with no device held
    (`build_current`), then installed with `adb install` inside the device's lease, with its sha256 so
    the report can prove what it judged. The Device agent never runs Gradle."""
    spent = device_budget_stop(target)
    if spent:
        return spent
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
               "AW_FLOW": str(sibling or ""), "AW_PHASE": phase, "AW_TARGET": str(target),
               "AW_REPLAY_STATE": str(folder.parent / STATE_DIR)}
        command = [str(flow)] if os.access(flow, os.X_OK) else ["sh", str(flow)]
    else:
        env = None
        command = [maestro, "--device", serial, "test", "-e", f"AW_PHASE={phase}", "--test-output-dir", str(folder),
                   str(flow)]
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


def _run_alone(flow: Path, maestro: str, serial: str, folder: Path, target: Path, phase: str,
               timeout: int) -> tuple[int, str, dict[str, Any] | None, bool]:
    """One attempt of one flow; a yaml with `aw-events` has logcat read for the whole attempt.
    Returns (exit code, log, events, whether the event count failed it)."""
    check = event_check(flow)
    tap = LogcatTap(serial, [check]).start() if check is not None and "regex" in check else None
    try:
        code, log = _run_flow(flow, maestro, serial, folder, target, phase, timeout)
    finally:
        if tap is not None:
            tap.stop()
    # Logcat is read from a little before the start: only what this attempt logged counts.
    window = (tap.started, float("inf")) if tap is not None else None
    return _with_events(check, tap, window, folder, flow, phase, code, log)


def _with_events(check: dict[str, Any] | None, tap: "LogcatTap | None", window: tuple[float, float] | None,
                 folder: Path, flow: Path, phase: str, code: int, log: str) -> tuple[int, str, dict[str, Any] | None, bool]:
    code, events, failed = _finish_events(check, tap, window, folder, flow, phase, code, log)
    return code, log, events, failed


BATCH_DIR = "_batch"


def _case_start(case: Any) -> float | None:
    """A JUnit testcase's start (Maestro writes local time, whole seconds), as epoch seconds."""
    from datetime import datetime

    try:
        return datetime.fromisoformat(str(case.get("timestamp"))).timestamp() if case.get("timestamp") else None
    except ValueError:
        return None


def _run_batch(batch: list[Path], maestro: str, serial: str, out: Path, target: Path, timeout: int,
               phase: str = "after") -> dict[str, tuple[int, str, float, dict[str, Any] | None, bool]]:
    """The first attempt of several Maestro flows in ONE `maestro test` call: Maestro's startup (10-15 s)
    is paid once instead of once per flow. Each flow's output moves to `out/<flow>/` as if it ran alone,
    with a `maestro.out` of its own; JUnit gives each flow's result. Returns {flow name: (exit code, log,
    seconds)} for the flows the call decided; a flow it did not (Maestro died or the call ran past the
    flows' summed timeouts) is left out, and runs alone."""
    work = out / BATCH_DIR
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    report, console = work / "report.xml", work / "maestro.out"
    command = [maestro, "--device", serial, "test", "-e", f"AW_PHASE={phase}", "--test-output-dir", str(work / "out"),
               "--format", "junit", "--output", str(report), *(str(flow) for flow in batch)]
    checks = {flow.name: event_check(flow) for flow in batch}
    wanted = [check for check in checks.values() if check is not None and "regex" in check]
    tap = LogcatTap(serial, wanted).start() if wanted else None
    try:
        with console.open("w", encoding="utf-8") as handle:
            subprocess.run(command, cwd=target, stdout=handle, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                           timeout=timeout * len(batch), check=False)
    except (subprocess.TimeoutExpired, OSError):
        pass
    finally:
        if tap is not None:
            tap.stop()
    import xml.etree.ElementTree as ElementTree

    try:
        cases = list(ElementTree.parse(report).getroot().iter("testcase"))
    except (OSError, ElementTree.ParseError):
        cases = []
    lines = console.read_text(encoding="utf-8", errors="replace").splitlines() if console.is_file() else []
    by_name = {flow.name: flow for flow in batch}
    by_stem = {flow.stem: flow for flow in batch}
    names = [case.get("name") or "" for case in cases]
    starts = [_case_start(case) for case in cases]
    decided: dict[str, tuple[int, str, float, dict[str, Any] | None, bool]] = {}  # code, log, seconds, events, count off
    for index, case in enumerate(cases):
        name = case.get("name") or ""
        flow = by_name.get(Path(case.get("file") or "").name) or by_stem.get(name)
        if flow is None or flow.name in decided or names.count(name) > 1:
            continue  # two flows with the same `name:` share an output folder: each runs alone
        failure = case.find("failure")
        if failure is None:
            failure = case.find("error")
        passed = failure is None and (case.get("status") or "SUCCESS").upper() == "SUCCESS"
        folder = out / flow.stem
        shutil.rmtree(folder, ignore_errors=True)
        produced = next((path for path in (work / "out").glob(f"*/{name}") if path.is_dir()), None) if name else None
        if produced is not None:
            shutil.move(str(produced), str(folder))
        folder.mkdir(parents=True, exist_ok=True)
        own = [line for line in lines if re.match(rf"\s*\[\w+\] {re.escape(name)}(?: \(|$)", line)]
        message = "" if failure is None else (failure.text or failure.get("message") or "").strip()
        log = folder / "maestro.out"
        log.write_text("\n".join([f"(one `maestro test` call with {len(batch)} flows)", *own,
                                  *([message] if message else []),
                                  *([_tail(next(folder.rglob("maestro.log")), 40)] if not passed and any(folder.rglob("maestro.log")) else [])])
                       + "\n", encoding="utf-8")
        try:
            seconds = float(case.get("time") or 0)
        except ValueError:
            seconds = 0.0
        code = 0 if passed else 1
        check = checks.get(flow.name)
        window = None
        if check is not None and "regex" in check:
            # The flow's own lines: from its start to the next flow's (a second of slack: JUnit's starts
            # are whole seconds and a flow logs nothing before its launchApp). No start: it runs alone.
            if starts[index] is None:
                shutil.rmtree(folder, ignore_errors=True)
                continue
            following = next((start for start in starts[index + 1:] if start is not None), None)
            window = (starts[index], following + EVENT_WINDOW_SLACK if following is not None else float("inf"))
        code, logged, events, events_failed = _with_events(check, tap, window, folder, flow, phase, code, str(log))
        decided[flow.name] = (code, logged, seconds, events, events_failed)
    shutil.rmtree(work, ignore_errors=True)
    return decided


def replay(target: Path, serial: str | None = None, phase: str = "after", install_base_apk: bool | None = None,
           lease_timeout: float = 1200.0, only: list[str] | None = None, fresh: bool = False,
           full: bool = False) -> dict[str, Any]:
    """Run every saved flow and file what it captured under `media/<phase>/`, holding the device's
    lease the whole time (another run's Device stage waits, and so does this replay for theirs).

    `after` installs the current build first. `before` installs the cached base APK when the base
    build has one (`install_base_apk`, default: when no `before` agent installed it already, i.e. the
    late `before` after the `after` round), else runs on the build already installed. Returns what was
    captured per flow, with the time each step took.

    A flow that passed on this very build (same source, same APK, same flow files) and whose captures are
    still filed is not run again (`reused`); `fresh` runs every flow. `only` runs just those flows (names
    with or without the extension) and keeps every other flow's last result.

    On a new source after an `after` replay (a fix round), the flows that failed last time and the ones
    whose ids or texts the change touched run first, and the first real failure stops the replay: the
    rest are `not_run` until it passes, and the round that passes runs them all for the PR. `full` runs
    every flow whatever fails. A flow that fails and then passes with nothing changed is `flaky`, never a
    failure; flaky in `UNSTABLE_FLAKES` replays, it is `unstable` and no longer run."""
    spent = device_budget_stop(target)
    if spent:
        return spent
    from android_workflow.emulator import status as device_status
    from android_workflow.locks import device_lease

    if phase not in {"before", "after"}:
        return {"status": "failed", "reason": "phase must be before or after"}
    if only:
        unknown = sorted({Path(name).stem for name in only} - {flow.stem for flow in flows(target)})
        if unknown:
            return {"status": "failed", "reason": "no saved flow named " + ", ".join(unknown),
                    "flows": [flow.name for flow in flows(target)]}
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
        return _replay(target, serial, phase, install_base_apk, built, only=only, fresh=fresh, full=full)


def _kept_results(saved: list[Path], earlier: dict[str, Any], phase: str, filed: Path, only: list[str] | None,
                  fresh: bool, same_build: bool) -> dict[str, dict[str, Any]]:
    """The flows this replay does not run, with the result each keeps from the last replay of this phase.

    `earlier` holds only results on the same source. With `only`, every other flow keeps its last result
    as it is. Otherwise a flow is kept when it passed (on the base build: captured its before) on the
    same APK, its files are unchanged, and everything it captured is still filed."""
    wanted = {Path(name).stem for name in only or []}
    kept: dict[str, dict[str, Any]] = {}
    for flow in saved:
        last = earlier.get(flow.name)
        if not isinstance(last, dict) or last.get("digest") != flow_digest(flow):
            continue
        if wanted:
            if flow.stem not in wanted:
                kept[flow.name] = last
            continue
        good = {"passed", "partial"} if phase == "before" else {"passed"}
        captured = last.get("captured") or []
        if fresh or not same_build or last.get("status") not in good or not captured:
            continue
        if all(filed.is_dir() and any(filed.glob(name + ".*")) for name in captured):
            kept[flow.name] = last
    return kept


def _replay(target: Path, serial: str, phase: str, install_base_apk: bool | None,
            built: dict[str, Any] | None = None, only: list[str] | None = None, fresh: bool = False,
            full: bool = False) -> dict[str, Any]:
    from android_workflow.cli import append_stage_log, read_json, snapshot_tree, source_fingerprint
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
    out.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    install_seconds = 0.0
    result_file = agent_dir / (REPLAY_FILE if phase == "after" else f"replay-{phase}.json")
    fingerprint = source_fingerprint(target)
    previous = read_json(result_file) if result_file.is_file() else {}
    earlier = {item.get("flow"): item for item in previous.get("flows") or []
               if isinstance(item, dict) and previous.get("fingerprint") == fingerprint}
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
    build_id = str(base) if base is not None else str((built or {}).get("sha256") or "")
    previous_id = str(previous.get("base_apk") or "") if phase == "before" else str(previous.get("sha256") or "")
    # after: `earlier` already means the same source; before: the same base APK
    same_build = build_id == previous_id and (phase == "after" or bool(build_id))
    kept = _kept_results(saved, earlier, phase, media_dir(target) / phase, only=only, fresh=fresh, same_build=same_build)
    tree = snapshot_tree(target) if phase == "after" else None
    # A fix round (a new source after an `after` replay): what failed and what the change touched first.
    subset = phase == "after" and not only and not full and previous.get("phase") == "after" \
        and bool(previous.get("fingerprint")) and previous.get("fingerprint") != fingerprint
    first: list[str] = []
    if subset:
        failed_last = {item.get("flow") for item in previous.get("flows") or []
                       if isinstance(item, dict) and item.get("status") in {"failed", "unstable"}}
        touched = affected(saved, changed_text(target, previous.get("tree"), tree))
        first = [flow.name for flow in saved if flow.name in failed_last or flow.name in touched]
    order = sorted(saved, key=lambda flow: (flow.name not in first, flow.name))
    health_path = agent_dir / FLOW_HEALTH_FILE
    health = (read_json(health_path) if health_path.is_file() else {}).get("flows") or {}
    app = str(device_config.get("application_id") or "") or None
    for folder in out.iterdir():  # a flow that runs again starts from an empty folder
        if folder.is_dir() and folder.name not in {Path(name).stem for name in kept}:
            shutil.rmtree(folder, ignore_errors=True)
    (out / STATE_DIR).mkdir(exist_ok=True)
    # What a flow may file: the names it declares, plus (older flows) the names the before capture used.
    known = sorted({path.stem for path in before.glob("*") if path.is_file()}) if before.is_dir() else []
    owned = sorted({name for names in declared.values() for name in names} | (set(known) if phase == "after" else set()))
    keep_names = {name for item in kept.values() for name in item.get("captured") or []}
    _drop_phase(target, phase, [name for name in owned if name not in keep_names])  # what runs again is captured again

    def flakes_of(flow: Path, digest: str) -> int:
        entry = health.get(flow.name) or {}
        return int(entry.get("flaky") or 0) if entry.get("digest") == digest else 0

    def streak_of(flow: Path, digest: str) -> int:
        last = earlier.get(flow.name) or {}
        return int(last.get("failed_streak") or 0) if last.get("digest") == digest else 0

    # The Maestro flows that run get their first attempt in one `maestro test` call (startup paid once).
    # A fix round keeps one call per flow: it stops at the first real failure, before the rest run.
    batch: dict[str, tuple[int, str, float, dict[str, Any] | None, bool]] = {}
    batch_info: dict[str, Any] = {}
    batch_since = time.time()
    if not subset:
        todo = [flow for flow in order if flow.suffix != ".sh" and flow.name not in kept and not launch_then_link(flow)
                and not (phase == "after" and flakes_of(flow, flow_digest(flow)) >= UNSTABLE_FLAKES)
                and streak_of(flow, flow_digest(flow)) < STALE_FAILS]
        if len(todo) > 1:
            batch_started = time.monotonic()
            batch = _run_batch(todo, maestro, serial, out, target, flow_timeout, phase)
            batch_info = {"flows": [flow.name for flow in todo], "seconds": round(time.monotonic() - batch_started, 1),
                          "alone": [flow.name for flow in todo if flow.name not in batch]}
    results = []
    stopped: str | None = None
    device_errors = 0
    for flow in order:
        if flow.name in kept:
            results.append({**kept[flow.name], "reused": True, "seconds": 0.0})
            continue
        digest = flow_digest(flow)
        if stopped and not (flow.name in batch and batch[flow.name][0] == 0):  # a flow that already passed keeps it
            results.append({"flow": flow.name, "status": "not_run", "digest": digest, "captured": [], "attempts": 0,
                            "seconds": 0.0, "reason": stopped})
            continue
        folder = out / flow.stem
        folder.mkdir(parents=True, exist_ok=True)
        last = earlier.get(flow.name) or {}
        flakes = flakes_of(flow, digest)
        if phase == "after" and flakes >= UNSTABLE_FLAKES:
            # It failed and then passed with nothing changed, again: its result says nothing about the change.
            results.append({"flow": flow.name, "status": "unstable", "skipped": True, "digest": digest,
                            "flaky_count": flakes, "captured": [], "attempts": 0, "seconds": 0.0,
                            "reason": f"flaky in {flakes} replays: not scripted any more; verify its AC by hand "
                                      "(or list it under Not verified) or repair the flow, an edit runs it again"})
            continue
        unsafe = launch_then_link(flow)
        if unsafe:  # its captures could show the previous screen: fixed before it takes device time
            results.append({"flow": flow.name, "status": "failed", "cause": "flow", "digest": digest,
                            "captured": [], "attempts": 0, "seconds": 0.0, "reason": unsafe})
            continue
        streak = streak_of(flow, digest)
        if streak >= STALE_FAILS:
            # Same flow, same source: a third run fails the same way. Only an edit to the flow (or the code) reruns it.
            results.append({"flow": flow.name, "status": "failed", "skipped": True, "digest": digest,
                            "failed_streak": streak, "captured": [], "attempts": 0, "seconds": 0.0,
                            "reason": f"failed {streak} replays unchanged on this source: repair the flow or delete it",
                            **({"hints": last["hints"]} if last.get("hints") else {})})
            continue
        if flow.name in batch:
            code, log, first_seconds, events, events_off = batch[flow.name]
            flow_started = time.monotonic() - first_seconds
            attempt_started = batch_since
        else:
            flow_started = time.monotonic()
            attempt_started = time.time()
            code, log, events, events_off = _run_alone(flow, maestro, serial, folder, target, phase, flow_timeout)
        attempts = 1
        first_failure: dict[str, Any] | None = None
        # A real failure on the new build is rerun once, from a clean app and Maestro driver. On the base
        # build a flow that looks for the new UI fails by design, so a second attempt only doubles the wait.
        if code != 0 and phase == "after":
            first_failure = {"exit_code": code, "cause": "events" if events_off else
                             failure_cause(code, failure_text(Path(log), folder, serial, attempt_started)),
                             "log_tail": _tail(Path(log), 8)}
            _reset_device(serial, app)
            attempt_started = time.time()
            code, log, events, events_off = _run_alone(flow, maestro, serial, folder, target, phase, flow_timeout)
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
        events = events or _event_count(folder, flow.stem)
        status = "passed" if code == 0 and (captured or metrics or events or phase == "before" and not allowed) else \
            "partial" if phase == "before" and captured else "failed"
        hints = flow_hints(flow, _tail(Path(log), 200)) if status == "failed" else []
        cause = ("events" if events_off else failure_cause(code, failure_text(Path(log), folder, serial, attempt_started))
                 if code else "step") if status == "failed" else None
        # Failed, then passed with nothing changed: in this replay (the retry) or since the last one on this build.
        flaky = phase == "after" and status == "passed" and (first_failure is not None or (
            same_build and last.get("digest") == digest and last.get("status") == "failed" and not last.get("skipped")))
        if flaky:
            health[flow.name] = {"digest": digest, "flaky": flakes + 1}
        results.append({"flow": flow.name, "status": status, "exit_code": code, "captured": captured,
                        "digest": digest, **({"failed_streak": streak + 1} if status == "failed" else {}),
                        **({"cause": cause} if cause else {}),
                        **({"flaky": True, "flaky_count": flakes + 1} if flaky else {}),
                        **({"first_failure": first_failure} if flaky and first_failure else {}),
                        **({"hints": hints} if hints else {}),
                        "attempts": attempts, "seconds": round(time.monotonic() - flow_started, 1),
                        **({"batched": True} if flow.name in batch else {}),
                        **({"frames": metrics} if metrics else {}),
                        **({"events": events} if events else {}),
                        **({"log_tail": _tail(Path(log))} if code else {}),
                        **({"reason": "the flow captured nothing it declares"} if code == 0 and not captured and status == "failed" else {})})
        if phase == "after" and status == "failed":
            device_errors = device_errors + 1 if cause == "device" else 0
            if device_errors >= DEVICE_ERRORS_IN_A_ROW:
                stopped = f"{device_errors} flows in a row failed on device errors (Maestro or adb, not the app)"
            elif subset and cause != "device":
                stopped = f"{flow.name} failed ({cause}): the other flows run once it passes"
        elif status == "passed":
            device_errors = 0
    results.sort(key=lambda item: str(item.get("flow")))
    if phase == "after":
        health_path.write_text(json.dumps({"flows": health}, indent=2) + "\n", encoding="utf-8")
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
    named = {kind: [str(item["flow"]) for item in results if test(item)] for kind, test in {
        "flaky": lambda item: item.get("flaky") and not item.get("reused"),
        "failed": lambda item: item.get("status") == "failed" and item.get("cause") != "device",
        "device_errors": lambda item: item.get("status") == "failed" and item.get("cause") == "device",
        "not_run": lambda item: item.get("status") == "not_run",
        "unstable": lambda item: item.get("status") == "unstable",
    }.items()}
    total = round(time.monotonic() - started, 1)
    build_seconds = 0.0 if (built or {}).get("cached") else float((built or {}).get("build_seconds") or 0)
    flows_seconds = round(total - install_seconds, 1)
    result = {
        "status": status, "phase": phase, "serial": serial, "fingerprint": fingerprint,
        "flows": results, **{key: value for key, value in named.items() if value},
        **({"subset": {"first": first, "stopped": stopped}} if subset else {}),
        **({"batch": batch_info} if batch_info else {}),
        **({"tree": tree} if tree else {}),
        "identical_to_before": identical, "missing_from_after" if phase == "after" else "missing": missing,
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
    if named["device_errors"] and not named["failed"] and not any(item.get("cause") == "app_crash" for item in results):
        result["next"] = ("the flows failed on device errors (Maestro or adb), not on the change: replay again once "
                          "(the CLI resets the app and the driver); never a FAIL on them, list them under Not verified")
    elif named["not_run"]:
        result["next"] = ("a flow failed and stopped the replay: judge the failure (FAIL when the change is at fault, "
                          "or fix the flow and replay it with --flow); the not_run flows run once it passes. Never "
                          "drive them by hand this round")
    compare(target)
    if phase == "after" and status == "replayed":
        from android_workflow.flows import remember

        result["library"] = remember(target)  # the next ticket on this screen copies these flows
    result_file.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    labels = {"failed": "failed", "device_errors": "device error", "flaky": "flaky", "unstable": "unstable",
              "not_run": "not run after a failure"}
    counts = [f"{len(named[key])} {label}" for key, label in labels.items() if named[key]]
    if subset:
        counts.insert(0, f"subset: {len(first)} first" if first else "subset")
    if batch:
        counts.insert(0, f"{len(batch)} in one maestro call ({batch_info['seconds']}s)")
    note = f"{passed}/{len(results)} flow(s) {'replayed on the new build' if phase == 'after' else 'run on the base build'}" \
           f" in {flows_seconds}s" + (f" ({'; '.join(counts)})" if counts else "") \
           + (f" (build {build_seconds}s before the device, install {install_seconds}s)" if phase == "after" else "")
    append_stage_log(agent_dir, "T7", "replayed" if phase == "after" else "flows_ready", "cli", note)
    return result


def launch_app(target: Path, serial: str, pins: str | None = None, link: str | None = None,
               wait_seconds: float = 20) -> dict[str, Any]:
    """`CLI device launch`: a cold start of the app on `serial`, with feature-flag pins when given, then the
    deep link on the running process. Pins only apply on a cold start and a deep link in the same intent
    left the root screen blank, so: force-stop, launch with the pins, confirm them in logcat, then the link.

    `device` in `.ai/android-workflow.json`: `application_id`, `launch_activity`, and for pins
    `pins_extra` (the string extra that carries the JSON) and `pins_log_tag` (the logcat tag that confirms)."""
    spent = device_budget_stop(target)
    if spent:
        return spent
    from android_workflow.cli import read_json
    from android_workflow.emulator import adb_path

    config_path = cache_dir(target) / "project-config.json"
    device = {**((read_json(config_path) if config_path.is_file() else {}).get("device") or {}),
              **(read_json(target / ".ai" / "android-workflow.json").get("device") or {}
                 if (target / ".ai" / "android-workflow.json").is_file() else {})}
    app, activity = device.get("application_id"), device.get("launch_activity")
    adb = adb_path(target)
    if not adb or not app:
        return {"status": "unavailable", "reason": "no adb or no device.application_id in .ai/android-workflow.json"}
    if pins:
        try:
            json.loads(pins)
        except ValueError as error:
            raise ValueError(f"--pins is not valid JSON: {error}") from error
        if not device.get("pins_extra"):
            raise ValueError("--pins needs device.pins_extra (the launch extra that carries them) in .ai/android-workflow.json")

    def shell(*args: str, timeout: float = 60) -> subprocess.CompletedProcess[str]:
        return subprocess.run([adb, "-s", serial, *args], text=True, capture_output=True, check=False, timeout=timeout)

    shell("shell", "am", "force-stop", app)
    shell("logcat", "-c")
    component = f"{app}/{activity}" if activity else None
    start = ["am", "start", "-W"] + (["-n", component] if component else ["-a", "android.intent.action.MAIN",
                                                                         "-c", "android.intent.category.LAUNCHER", app])
    if pins:
        start += ["--es", device["pins_extra"], shlex.quote(pins)]
    launched = shell("shell", " ".join(start))
    result: dict[str, Any] = {"status": "launched", "serial": serial, "app": app,
                              "output": (launched.stdout or launched.stderr).strip()[-300:]}
    if launched.returncode != 0 or "Error" in launched.stdout:
        return {**result, "status": "failed"}
    tag = device.get("pins_log_tag")
    if pins and tag:
        deadline = time.monotonic() + wait_seconds
        applied = ""
        while time.monotonic() < deadline and not applied:
            applied = shell("logcat", "-d", "-s", str(tag)).stdout.strip()
            if not applied:
                time.sleep(1)
        result["pins"] = applied.splitlines()[-1] if applied else None
        if not applied:
            result["status"] = "pins_not_confirmed"
            result["reason"] = (f"no {tag} line in logcat after {int(wait_seconds)}s: the pins did not arrive "
                                "(API < 29, a misspelled extra, or the app was already running)")
            return result
    if link:
        time.sleep(2)
        opened = shell("shell", f"am start -W -a android.intent.action.VIEW -d {shlex.quote(link)} {app}")
        result["link"] = (opened.stdout or opened.stderr).strip()[-300:]
        if opened.returncode != 0 or "Error" in opened.stdout:
            result["status"] = "link_failed"
    return result

def device_budget_stop(target: Path) -> dict[str, Any] | None:
    """Mid-round: once the run spent `device.budget_minutes` (when set) plus `DEVICE_BUDGET_GRACE_SECONDS`
    on the device, every device command answers this instead of driving it. The grace lets a round that
    crossed the budget finish the check in hand (its last capture) instead of losing it."""
    try:
        from android_workflow.decide import DEVICE_BUDGET_GRACE_SECONDS, device_stop

        stop = device_stop(target, run_dir(target), grace=DEVICE_BUDGET_GRACE_SECONDS)
    except (FileNotFoundError, OSError, ValueError):
        return None
    if not stop:
        return None
    return {"status": "budget_spent", "reason": stop,
            "next": "stop driving the device: write device-report.md now with what was verified (PARTIAL or "
                    "BLOCKED, `Not verified` lists the rest) and log the Device stage. The developer can raise "
                    "or clear (0 = no limit) device.budget_minutes in .ai/android-workflow.json for more time"}
