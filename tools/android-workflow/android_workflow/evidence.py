"""Capture stills and video into <repo>/.ai/workflow/<ticket>/media/."""

from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from android_workflow.paths import (
    COMPARE_NAME,
    MANIFEST_NAME,
    media_dir,
    run_dir,
    ticket_slug,
    workflow_root,
)

PHASES = ("before", "after")
KINDS = ("screenshot", "video")
NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def evidence_name(raw: str | None) -> str:
    slug = ticket_slug(raw or "screen")
    if not NAME_RE.match(slug):
        raise ValueError(f"invalid evidence name: {raw!r}")
    return slug


def manifest_path(target: Path) -> Path:
    return media_dir(target) / MANIFEST_NAME


def load_manifest(target: Path) -> dict[str, Any]:
    path = manifest_path(target)
    if not path.exists():
        return {"schema_version": 1, "items": []}
    return json.loads(path.read_text(encoding="utf-8"))


def save_manifest(target: Path, manifest: dict[str, Any]) -> None:
    path = manifest_path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def record_item(target: Path, item: dict[str, Any]) -> None:
    manifest = load_manifest(target)
    items = [
        existing
        for existing in manifest.get("items") or []
        if not (existing.get("name") == item["name"] and existing.get("phase") == item["phase"])
    ]
    items.append(item)
    manifest["items"] = items
    save_manifest(target, manifest)


def adb_parts(config: dict[str, Any] | None) -> list[str]:
    raw = "adb"
    if config:
        raw = str((config.get("tools") or {}).get("adb") or "adb")
    return shlex.split(raw)


def adb_serial(adb: list[str]) -> str | None:
    result = subprocess.run([*adb, "devices"], text=True, capture_output=True, check=False, timeout=15)
    for line in result.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            return parts[0]
    return None


def adb_cmd(config: dict[str, Any] | None) -> list[str]:
    parts = adb_parts(config)
    serial = adb_serial(parts)
    if serial:
        return [*parts, "-s", serial]
    return parts


def dest_path(target: Path, phase: str, name: str, kind: str) -> Path:
    if phase not in PHASES:
        raise ValueError("phase must be before or after")
    if kind not in KINDS:
        raise ValueError("kind must be screenshot or video")
    suffix = ".png" if kind == "screenshot" else ".mp4"
    path = media_dir(target) / phase / f"{evidence_name(name)}{suffix}"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def relative_to_run(target: Path, path: Path) -> str:
    return str(path.relative_to(run_dir(target)))


def try_android_capture(destination: Path) -> bool:
    binary = shutil.which("android")
    if not binary:
        return False
    result = subprocess.run(
        [binary, "screen", "capture", f"--output={destination}"],
        capture_output=True,
        check=False,
        timeout=30,
    )
    return result.returncode == 0 and destination.is_file() and destination.stat().st_size > 32


def capture_still(target: Path, config: dict[str, Any] | None, phase: str, name: str) -> dict[str, Any]:
    destination = dest_path(target, phase, name, "screenshot")
    tool = "android-cli"
    ok = try_android_capture(destination)
    if not ok:
        tool = "adb-screencap"
        cmd = [*adb_cmd(config), "exec-out", "screencap", "-p"]
        result = subprocess.run(cmd, capture_output=True, check=False, timeout=30)
        if result.returncode != 0 or not result.stdout.startswith(b"\x89PNG"):
            raise ValueError("screenshot failed; is a device connected?")
        destination.write_bytes(result.stdout)
        ok = True
    item = {
        "name": evidence_name(name),
        "phase": phase,
        "kind": "screenshot",
        "path": relative_to_run(target, destination),
        "tool": tool,
        "bytes": destination.stat().st_size,
        "captured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ok": True,
    }
    record_item(target, item)
    return {**item, "abs_path": str(destination)}


def capture_video(target: Path, config: dict[str, Any] | None, phase: str, name: str, seconds: int = 8) -> dict[str, Any]:
    duration = max(2, min(int(seconds), 30))
    destination = dest_path(target, phase, name, "video")
    remote = f"/sdcard/aw-{evidence_name(name)}.mp4"
    adb = adb_cmd(config)
    record = subprocess.run(
        [*adb, "shell", "screenrecord", "--time-limit", str(duration), remote],
        capture_output=True,
        check=False,
        timeout=duration + 20,
    )
    if record.returncode != 0:
        raise ValueError((record.stderr or record.stdout or b"screenrecord failed").decode("utf-8", "replace").strip())
    pull = subprocess.run([*adb, "pull", remote, str(destination)], capture_output=True, check=False, timeout=30)
    subprocess.run([*adb, "shell", "rm", "-f", remote], capture_output=True, check=False, timeout=15)
    if pull.returncode != 0 or not destination.is_file() or destination.stat().st_size < 64:
        raise ValueError("video capture failed; emulator may not support screenrecord")
    item = {
        "name": evidence_name(name),
        "phase": phase,
        "kind": "video",
        "path": relative_to_run(target, destination),
        "tool": "adb-screenrecord",
        "bytes": destination.stat().st_size,
        "seconds": duration,
        "captured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ok": True,
    }
    record_item(target, item)
    return {**item, "abs_path": str(destination)}


def ingest(target: Path, phase: str, name: str, source: Path) -> dict[str, Any]:
    src = source.expanduser().resolve()
    if not src.is_file():
        raise ValueError(f"file not found: {src}")
    suffix = src.suffix.lower()
    if suffix == ".png":
        kind = "screenshot"
    elif suffix in {".mp4", ".webm"}:
        kind = "video"
    else:
        raise ValueError("ingest accepts .png, .mp4, or .webm")
    destination = media_dir(target) / phase / f"{evidence_name(name)}{suffix}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, destination)
    item = {
        "name": evidence_name(name),
        "phase": phase,
        "kind": kind,
        "path": relative_to_run(target, destination),
        "tool": "ingest",
        "source": str(src),
        "bytes": destination.stat().st_size,
        "captured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ok": True,
    }
    record_item(target, item)
    return {**item, "abs_path": str(destination)}


def baseline(target: Path, from_ticket: str) -> dict[str, Any]:
    source_root = workflow_root(target) / ticket_slug(from_ticket) / "media" / "after"
    if not source_root.is_dir():
        raise ValueError(f"no after/ media in {from_ticket}")
    copied: list[str] = []
    for src in sorted(source_root.iterdir()):
        if src.suffix.lower() not in {".png", ".mp4", ".webm"}:
            continue
        name = src.stem
        kind = "screenshot" if src.suffix.lower() == ".png" else "video"
        destination = media_dir(target) / "before" / f"{name}{src.suffix.lower()}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, destination)
        record_item(
            target,
            {
                "name": name,
                "phase": "before",
                "kind": kind,
                "path": relative_to_run(target, destination),
                "tool": "baseline",
                "source_ticket": ticket_slug(from_ticket),
                "bytes": destination.stat().st_size,
                "captured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "ok": True,
            },
        )
        copied.append(name)
    if not copied:
        raise ValueError(f"{from_ticket} after/ has no png/mp4")
    return {"from": ticket_slug(from_ticket), "copied": copied, "phase": "before"}


def compare(target: Path, previous: str | None = None) -> dict[str, Any]:
    run = run_dir(target)
    media = media_dir(target)
    names = sorted(
        {
            path.stem
            for phase in PHASES
            for path in (media / phase).glob("*")
            if path.suffix.lower() in {".png", ".mp4", ".webm"}
        }
    )
    previous_after = None
    if previous:
        previous_after = workflow_root(target) / ticket_slug(previous) / "media" / "after"
    rows: list[dict[str, Any]] = []
    lines = [
        "# Visual compare",
        "",
        f"- Current run: `{run.name}`",
        f"- Previous after: `{previous}`" if previous else "- Previous after: none",
        "",
        "| name | before | after | previous after |",
        "|---|---|---|---|",
    ]
    for name in names:
        before = next((media / "before").glob(f"{name}.*"), None)
        after = next((media / "after").glob(f"{name}.*"), None)
        prior = next(previous_after.glob(f"{name}.*"), None) if previous_after and previous_after.is_dir() else None
        rows.append(
            {
                "name": name,
                "before": str(before.relative_to(run)) if before else None,
                "after": str(after.relative_to(run)) if after else None,
                "previous_after": str(prior.relative_to(workflow_root(target))) if prior else None,
            }
        )
        lines.append(
            f"| `{name}` | {rows[-1]['before'] or '—'} | {rows[-1]['after'] or '—'} | {rows[-1]['previous_after'] or '—'} |"
        )
    if not names:
        lines.append("| *(none)* | — | — | — |")
    lines.extend(
        [
            "",
            "Open the files on disk. Do not paste PNG/MP4 bytes into chat.",
            "",
        ]
    )
    path = media / COMPARE_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"path": str(path), "pairs": rows}


def evidence_summary(agent_dir: Path) -> str | None:
    media = agent_dir / "media"
    compare_file = media / COMPARE_NAME
    if compare_file.exists():
        return f"See `{compare_file.relative_to(agent_dir)}` and `{media.name}/`."
    shots = list(media.rglob("*.png")) + list(media.rglob("*.mp4")) if media.is_dir() else []
    if not shots:
        return None
    bullets = "\n".join(f"- `{path.relative_to(agent_dir)}`" for path in sorted(shots)[:12])
    return bullets


def dispatch_evidence(target: Path, args: Any, config: dict[str, Any] | None) -> dict[str, Any]:
    action = args.action
    if action == "list":
        return load_manifest(target)
    if action == "ingest":
        if not args.file:
            raise ValueError("ingest requires --file")
        return ingest(target, args.phase, args.name, args.file)
    if action == "baseline":
        if not args.from_ticket:
            raise ValueError("baseline requires --from TICKET")
        return baseline(target, args.from_ticket)
    if action == "compare":
        return compare(target, args.previous)
    if action == "capture":
        if args.kind == "video":
            return capture_video(target, config, args.phase, args.name, args.seconds)
        return capture_still(target, config, args.phase, args.name)
    raise ValueError(f"unknown evidence action: {action}")
