"""Real time for the office page: the files the change touches, and a watcher that keeps the page current.

Agents edit files and write run artifacts between CLI commands, so the page would only move when a
command runs. While a run is live, a small background watcher rewrites `office-data.js` every couple
of seconds (only when something changed); the page loads that script instead of reloading itself.

Safety: git is read with `--no-optional-locks`, so the watcher never takes `index.lock` from an agent's
own git command. Only one watcher runs per app (an exclusive file lock), and it stops by itself when
the run ends, when nothing changes for a long time, when the run folder disappears, or after a
maximum lifetime. Any later CLI command starts it again.
"""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import time
from pathlib import Path, PurePosixPath
from typing import Any

from android_workflow.paths import cache_dir, current_ticket_id, workflow_root

TOOLKIT_ROOT = Path(__file__).resolve().parent.parent
WATCH_INTERVAL_SECONDS = 2.0
IDLE_EXIT_SECONDS = 45 * 60
MAX_LIFETIME_SECONDS = 12 * 3600
MAX_CHANGES = 200
GIT_TIMEOUT_SECONDS = 10
DISABLE_ENV = "ANDROID_WORKFLOW_NO_WATCH"


def _git(target: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "--no-optional-locks", "-c", "core.quotepath=off", "-C", str(target), *args],
            text=True, capture_output=True, check=False, timeout=GIT_TIMEOUT_SECONDS,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 else None


def _unquote(path: str) -> str:
    path = path.strip()
    if len(path) >= 2 and path[0] == '"' and path[-1] == '"':
        try:
            return json.loads(path)
        except ValueError:
            return path[1:-1]
    return path


def _base(target: Path) -> str:
    try:
        value = json.loads((cache_dir(target) / "repo-map.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        value = {}
    base = value.get("base_commit") if isinstance(value, dict) else None
    return str(base) if base else "HEAD"


def _json_file(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _hot_dirs(target: Path, tracked: list[str]) -> list[str]:
    """Module folders where new files are likely: those the change already touches, the likely files'
    and the team's. Listing untracked files there is cheap; a whole large repository is not."""
    from android_workflow.paths import run_dir

    sources = list(tracked)
    try:
        run = run_dir(target)
    except (OSError, ValueError):
        run = None
    modules: list[str] = []
    if run is not None:
        sources += [str(item) for item in _json_file(run / "t4-files.json").get("files") or []]
        change_set = _json_file(run / "change-set-map.json")
        sources += [str(item.get("path") if isinstance(item, dict) else item) for item in change_set.get("candidate_files") or []]
        modules = [str(item) for item in change_set.get("affected_modules") or []]
        for item in _json_file(run / "team-plan.json").get("slices") or []:
            if isinstance(item, dict):
                sources += [str(path) for path in item.get("files") or []]
    folders: set[str] = set()
    for raw in sources:
        parts = PurePosixPath(raw).parts
        if "src" in parts and parts.index("src") > 0:
            folders.add(PurePosixPath(*parts[: parts.index("src")]).as_posix())
        elif len(parts) > 1:
            folders.add(PurePosixPath(*parts[:-1]).as_posix())
    for module in modules:
        name = module.strip(":").replace(":", "/")
        if name:
            folders.add(name)
    found = sorted(folder for folder in folders if ".." not in folder and (target / folder).is_dir())
    # Keep the outermost folders only (a module covers its own sub-folders).
    outer = [folder for folder in found if not any(folder.startswith(other + "/") for other in found if other != folder)]
    return outer[:40]


def _untracked_cache(target: Path) -> Path:
    return cache_dir(target) / "office-untracked.json"


def live_changes(target: Path, limit: int = MAX_CHANGES, full: bool = False) -> list[dict[str, Any]]:
    """Files the change touches (from the run's base commit to the working tree), newest edit first.

    Each item: path (relative to the app), status (A, M, D or ?? for new untracked), added and removed
    lines (None for binary files) and the file's mtime (None once deleted). New files are listed in
    the module folders the change touches; `full` lists the whole repository (slow on a big one) and
    keeps that list for the cheaper calls in between.
    """
    base = _base(target)
    raw = _git(target, "diff", "--raw", "--numstat", "--no-renames", "--relative", base, "--")
    if raw is None:
        return []
    found: dict[str, dict[str, Any]] = {}
    for line in raw.splitlines():
        if line.startswith(":"):
            meta, _, path = line.partition("\t")
            parts = meta.split()
            status = parts[4][:1] if len(parts) >= 5 else "M"
            found.setdefault(_unquote(path), {"status": status, "added": None, "removed": None})
        elif line.count("\t") >= 2:
            added, removed, path = line.split("\t", 2)
            item = found.setdefault(_unquote(path), {"status": "M", "added": None, "removed": None})
            item["added"] = int(added) if added.isdigit() else None
            item["removed"] = int(removed) if removed.isdigit() else None
    untracked: list[str] = []
    if full:
        listing = _git(target, "ls-files", "--others", "--exclude-standard")
        untracked = [_unquote(line) for line in (listing or "").splitlines() if line.strip()]
        try:
            cache = _untracked_cache(target)
            cache.parent.mkdir(parents=True, exist_ok=True)
            temporary = cache.with_name(f".{cache.name}.{os.getpid()}.tmp")
            temporary.write_text(json.dumps({"at": time.time(), "base": base, "files": untracked[:2000]}), encoding="utf-8")
            os.replace(temporary, cache)
        except OSError:
            pass
    else:
        folders = _hot_dirs(target, list(found))
        if folders:
            listing = _git(target, "ls-files", "--others", "--exclude-standard", "--", *folders)
            untracked = [_unquote(line) for line in (listing or "").splitlines() if line.strip()]
        cached = _json_file(_untracked_cache(target))
        if cached.get("base") == base and time.time() - float(cached.get("at") or 0) < 3600:
            untracked += [str(path) for path in cached.get("files") or [] if (target / str(path)).is_file()]
    for path in untracked:
        if path and path not in found:
            found[path] = {"status": "??", "added": _line_count(target / path), "removed": 0}
    items = []
    for path, item in found.items():
        if not path or ".ai" in PurePosixPath(path).parts:
            continue
        try:
            mtime = (target / path).stat().st_mtime
        except OSError:
            mtime = None
        items.append({"path": path, **item, "mtime": round(mtime, 3) if mtime else None})
    items.sort(key=lambda item: (item["mtime"] is None, -(item["mtime"] or 0), item["path"]))
    return items[:limit]


MAX_PATCH_LINES = 600
MAX_PATCH_BYTES = 400_000


def live_patches(target: Path, changes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Unified diffs of the changed files for the office's GitHub-like view: tracked files from the run's
    base commit, new files as all-added. Capped per file and in total, so the page stays light."""
    patches: dict[str, dict[str, Any]] = {}
    budget = MAX_PATCH_BYTES

    def keep(path: str, lines: list[str]) -> None:
        nonlocal budget
        truncated = len(lines) > MAX_PATCH_LINES
        lines = lines[:MAX_PATCH_LINES]
        size = sum(len(line) + 1 for line in lines)
        if size > budget:
            patches[path] = {"lines": [], "truncated": True}
            return
        budget -= size
        patches[path] = {"lines": lines, "truncated": truncated}

    tracked = [item["path"] for item in changes if item.get("status") != "??"]
    if tracked:
        raw = _git(target, "diff", "--no-color", "--no-renames", "--relative", "-U3", _base(target), "--", *tracked) or ""
        for block in raw.split("\ndiff --git ")[0:]:
            lines = block.splitlines()
            path = None
            for line in lines[:8]:
                if line.startswith("+++ ") and line[4:] != "/dev/null":
                    path = line[6:] if line.startswith("+++ b/") else line[4:]
                elif line.startswith("--- ") and line[4:] != "/dev/null" and path is None:
                    path = line[6:] if line.startswith("--- a/") else line[4:]
            if not path:
                continue
            if any(line.startswith("Binary files") for line in lines[:8]):
                patches[path] = {"lines": [], "binary": True}
                continue
            start = next((index for index, line in enumerate(lines) if line.startswith("@@")), len(lines))
            keep(path, lines[start:])
    for item in changes:
        if item.get("status") != "??" or item["path"] in patches:
            continue
        try:
            data = (target / item["path"]).read_bytes()[:200_000]
        except OSError:
            continue
        if b"\0" in data:
            patches[item["path"]] = {"lines": [], "binary": True}
            continue
        body = data.decode("utf-8", errors="replace").splitlines()
        keep(item["path"], [f"@@ -0,0 +1,{len(body)} @@", *("+" + line for line in body)])
    return patches


def _line_count(path: Path) -> int | None:
    try:
        with path.open("rb") as handle:
            data = handle.read(1_000_000)
    except OSError:
        return None
    if b"\0" in data:
        return None
    return data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)


# ---- the watcher ---------------------------------------------------------------------------------

def _lock_path(target: Path) -> Path:
    return cache_dir(target) / "office-watch.lock"


def _record_path(target: Path) -> Path:
    return cache_dir(target) / "office-watch.json"


def _read_record(target: Path) -> dict[str, Any]:
    try:
        value = json.loads(_record_path(target).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _is_watcher(pid: Any) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return False
    try:
        command = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], text=True, capture_output=True,
                                 check=False, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return False
    return "android_workflow.live" in command


def _run_is_live(target: Path) -> bool:
    from android_workflow.office import run_is_live

    ticket = current_ticket_id(target)
    return bool(ticket) and run_is_live(workflow_root(target) / ticket)


def ensure_watcher(target: Path) -> dict[str, Any] | None:
    """Start the watcher for a live run unless one is already running. Never raises."""
    try:
        if os.environ.get(DISABLE_ENV) or not workflow_root(target).is_dir() or not _run_is_live(target):
            return None
        record = _read_record(target)
        if _is_watcher(record.get("pid")):
            return record
        cache_dir(target).mkdir(parents=True, exist_ok=True)
        code = (
            "import sys; sys.path.insert(0, sys.argv[1]); "
            "from android_workflow.live import watch; watch(sys.argv[2])"
        )
        process = subprocess.Popen(
            [sys.executable, "-c", code, str(TOOLKIT_ROOT), str(target), "android_workflow.live"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True, cwd=str(target),
        )
        return {"pid": process.pid, "started": True}
    except Exception:  # noqa: BLE001 - the page is a convenience, never a gate
        return None


def stop_watcher(target: Path) -> bool:
    """Ask a running watcher to stop (used by `clean`). True when one was running."""
    record = _read_record(target)
    pid = record.get("pid")
    if not _is_watcher(pid):
        return False
    try:
        os.kill(int(pid), 15)
    except OSError:
        return False
    return True


def watch(target_text: str, interval: float = WATCH_INTERVAL_SECONDS) -> None:
    from android_workflow.office import write_live_data

    target = Path(target_text)
    lock_path = _lock_path(target)
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = lock_path.open("a+")
    except OSError:
        return
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return  # another watcher already serves this app
    me = os.getpid()
    started = last_change = time.time()
    record_path = _record_path(target)
    try:
        temporary = record_path.with_name(f".{record_path.name}.{me}.tmp")
        temporary.write_text(json.dumps({"pid": me, "started_at": started}) + "\n", encoding="utf-8")
        os.replace(temporary, record_path)
        last = None
        errors = 0
        last_full, full_cost, tick_cost = 0.0, 0.0, 0.0
        while True:
            now = time.time()
            if not lock_path.exists() or not workflow_root(target).is_dir():
                break  # `clean` removed the folder: nothing to serve
            if now - started > MAX_LIFETIME_SECONDS or now - last_change > IDLE_EXIT_SECONDS:
                break
            # A whole-repository scan for new files costs ~1s on a big app: do it rarely, never every tick.
            full = now - last_full >= max(30.0, 20 * full_cost)
            try:
                began = time.time()
                key, live = write_live_data(target, last, full_scan=full)
                if full:
                    last_full, full_cost = now, time.time() - began
                else:
                    tick_cost = time.time() - began
                errors = 0
            except Exception:  # noqa: BLE001 - a half-written file this tick; try again on the next one
                errors += 1
                if errors > 30:
                    break
                time.sleep(interval)
                continue
            if key != last:
                last, last_change = key, now
            if not live:
                break
            # A tick costs ~0.05s on a small app and ~0.3s on a very large one: stay around 10% of a core.
            time.sleep(max(interval, 8 * tick_cost))
    finally:
        try:
            if _read_record(target).get("pid") == me:
                record_path.unlink()
        except OSError:
            pass
        handle.close()
