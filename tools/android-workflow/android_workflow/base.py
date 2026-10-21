"""The base build behind the device `before`: built in its own checkout, cached by commit.

Building the base in the ticket's own checkout raced the Implementer: its Gradle runs deleted the
intermediates the base build was reading, and its edits could leak into the "before" APK. And a
fresh worktree builds cold (20+ minutes for a large app), once per ticket.

So the base is built elsewhere, once per commit:

* **One base checkout per repository**, `state_home()/base/<repo>/checkout`, a detached worktree that
  is never deleted. Moving it from yesterday's base to today's is an incremental build.
* **One APK per commit**, `state_home()/base/<repo>/apks/<sha>/`. Two runs on the same base share it;
  a later run on that base gets it at once.
* **One build at a time per repository** (a lock), inside a machine build slot (`locks.build_slot`).

Nothing here reads or writes the ticket's checkout except to learn its base commit.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from android_workflow.paths import KIT_ROOT, state_home

KEEP_APK_SETS = 6
DEFAULT_BASE_TIMEOUT = 3600  # the very first build of the base checkout is a cold one


def _git(cwd: Path, *args: str, timeout: float = 120) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(["git", "-C", str(cwd), *args], text=True, capture_output=True, check=False,
                              timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, 124, "", "timed out")


def repository(target: Path) -> dict[str, Any] | None:
    """`{id, common_dir, main}`: the repository behind `target`, shared by all its worktrees."""
    found = _git(target, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if found.returncode != 0 or not found.stdout.strip():
        return None
    common = Path(found.stdout.strip()).resolve()
    main = common.parent if common.name == ".git" else common
    digest = hashlib.sha1(str(common).encode("utf-8")).hexdigest()[:10]
    return {"id": f"{main.name}-{digest}", "common_dir": str(common), "main": str(main)}


def base_commit(target: Path) -> str | None:
    """What `before` means for this change: the commit the diff is measured against."""
    from android_workflow.cli import diff_base, git_base_commit

    try:
        commit = diff_base(target) or git_base_commit(target)
    except Exception:  # noqa: BLE001 - no run cache yet: HEAD
        commit = git_base_commit(target)
    if not commit:
        return None
    resolved = _git(target, "rev-parse", "--verify", f"{commit}^{{commit}}")
    return resolved.stdout.strip() if resolved.returncode == 0 else None


def home(repo_id: str) -> Path:
    return state_home() / "base" / repo_id


def checkout_path(repo_id: str) -> Path:
    return home(repo_id) / "checkout"


def apk_dir(repo_id: str, sha: str) -> Path:
    return home(repo_id) / "apks" / sha


def cached(repo_id: str, sha: str) -> list[dict[str, Any]]:
    """The APKs already built for `sha` (each still on disk), newest first; [] when there are none."""
    meta_path = apk_dir(repo_id, sha) / "meta.json"
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    apks = [item for item in meta.get("apks") or [] if Path(str(item.get("path"))).is_file()]
    if apks:
        try:
            os.utime(meta_path)  # recently used: pruned last
        except OSError:
            pass
    return apks


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _store(repo_id: str, sha: str, built: list[Path]) -> list[dict[str, Any]]:
    folder = apk_dir(repo_id, sha)
    folder.mkdir(parents=True, exist_ok=True)
    stored = []
    for source in built:
        name = source.name
        if (folder / name).exists():  # two flavors with the same file name
            name = f"{source.parent.name}-{name}"
        destination = folder / name
        shutil.copy2(source, destination)
        stored.append({"path": str(destination), "bytes": destination.stat().st_size,
                       "sha256": _sha256(destination), "built_from": str(source), "mtime": destination.stat().st_mtime})
    (folder / "meta.json").write_text(json.dumps({"sha": sha, "apks": stored, "at": time.time()}, indent=2) + "\n",
                                      encoding="utf-8")
    _prune(repo_id)
    return stored


def _prune(repo_id: str) -> None:
    root = home(repo_id) / "apks"
    sets = sorted((path for path in root.iterdir() if (path / "meta.json").is_file()),
                  key=lambda path: (path / "meta.json").stat().st_mtime, reverse=True) if root.is_dir() else []
    for old in sets[KEEP_APK_SETS:]:
        shutil.rmtree(old, ignore_errors=True)


def _bootstrap(main: Path, path: Path) -> dict[str, Any]:
    """What `feature_workspace.py` does for a new worktree: submodules and `local.properties`."""
    bin_dir = str(KIT_ROOT / "bin")
    if bin_dir not in sys.path:
        sys.path.insert(0, bin_dir)
    try:
        from feature_workspace import bootstrap_worktree  # type: ignore[import-not-found]
    except ImportError:
        return {}
    try:
        return bootstrap_worktree(str(main), str(path))
    except Exception as error:  # noqa: BLE001 - best effort, the build says what is missing
        return {"error": str(error)}


def prepare_checkout(target: Path, repo: dict[str, Any], sha: str) -> tuple[Path, dict[str, Any]]:
    """The base checkout at `sha`: created once, then only moved (its build outputs stay)."""
    path = checkout_path(repo["id"])
    report: dict[str, Any] = {}
    if path.exists() and _git(path, "rev-parse", "--is-inside-work-tree").returncode != 0:
        shutil.rmtree(path, ignore_errors=True)  # a folder git no longer knows: start over
    if not path.exists():
        _git(target, "worktree", "prune")
        path.parent.mkdir(parents=True, exist_ok=True)
        added = _git(target, "worktree", "add", "--detach", "--force", str(path), sha, timeout=600)
        if added.returncode != 0:
            raise RuntimeError("could not create the base checkout: " + (added.stderr.strip() or added.stdout.strip()))
        report = _bootstrap(Path(repo["main"]), path)
        report["created"] = True
    else:
        moved = _git(path, "checkout", "--detach", "--force", sha, timeout=600)
        if moved.returncode != 0:
            raise RuntimeError("could not move the base checkout: " + (moved.stderr.strip() or moved.stdout.strip()))
        if (path / ".gitmodules").is_file():
            _git(path, "submodule", "update", "--init", "--recursive", timeout=600)
    return path, report


def find_apks(root: Path, since: float) -> list[Path]:
    found = []
    for path in root.glob("**/build/outputs/apk/**/*.apk"):
        try:
            if path.stat().st_mtime >= since - 1 and "androidTest" not in path.parts:
                found.append(path)
        except OSError:
            continue
    return sorted(found, key=lambda item: item.stat().st_mtime, reverse=True)


def build(target: Path, command: str, config: dict[str, Any] | None = None, log: Path | None = None,
          timeout: float | None = None, on_wait: Callable[[str], None] | None = None) -> dict[str, Any]:
    """The base APKs for `target`'s base commit: from the cache, else built in the base checkout.

    Returns `{status: passed|failed, sha, apks, cached, ...}`; `on_wait` hears why it is waiting."""
    from android_workflow.locks import build_slot, exclusive

    repo = repository(target)
    sha = base_commit(target)
    if not repo or not sha:
        return {"status": "failed", "reason": "not a git repository with a base commit"}
    hit = cached(repo["id"], sha)
    if hit:
        return {"status": "passed", "sha": sha, "apks": hit, "cached": True, "repo": repo["id"]}
    limit = timeout or float(((config or {}).get("execution") or {}).get("base_build_timeout_seconds") or DEFAULT_BASE_TIMEOUT)
    lock = home(repo["id"]) / "build.lock"
    with exclusive(lock, on_wait=(lambda holder: on_wait(f"another run is building this repository's base (pid {holder.get('pid')})"))
                   if on_wait else None):
        hit = cached(repo["id"], sha)  # the run we waited for may have built this very commit
        if hit:
            return {"status": "passed", "sha": sha, "apks": hit, "cached": True, "repo": repo["id"]}
        try:
            path, report = prepare_checkout(target, repo, sha)
        except RuntimeError as error:
            return {"status": "failed", "sha": sha, "reason": str(error), "repo": repo["id"]}
        with build_slot("base build " + repo["id"], config,
                        on_wait=(lambda holders: on_wait("waiting for a free build slot: "
                                                         + ", ".join(str(item.get("label")) for item in holders)))
                        if on_wait else None):
            started = time.time()
            code = _run(command, path, log, limit)
        apks = find_apks(path, started) if code == 0 else []
        result: dict[str, Any] = {"status": "passed" if code == 0 and apks else "failed", "sha": sha, "exit_code": code,
                                  "checkout": str(path), "repo": repo["id"], "cached": False,
                                  "build_seconds": round(time.time() - started, 1)}
        if report:
            result["bootstrap"] = report
        if code == 0 and not apks:
            result["reason"] = "the build passed but wrote no APK under build/outputs/apk"
        elif code == 124:
            result["reason"] = f"the base build timed out after {int(limit)}s"
        elif code:
            result["reason"] = f"the base build failed (exit {code})"
        if result["status"] == "passed":
            result["apks"] = _store(repo["id"], sha, apks)
        return result


def _run(command: str, cwd: Path, log: Path | None, timeout: float) -> int:
    handle = log.open("w", encoding="utf-8") if log else open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    try:
        with handle:
            handle.write(f"$ {command}  (in {cwd})\n")
            handle.flush()
            result = subprocess.run(shlex.split(command) + (["--console=plain"] if "gradlew" in command else []),
                                    cwd=cwd, stdout=handle, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                    timeout=timeout, check=False)
        return result.returncode
    except subprocess.TimeoutExpired:
        return 124
    except OSError:
        return 127
