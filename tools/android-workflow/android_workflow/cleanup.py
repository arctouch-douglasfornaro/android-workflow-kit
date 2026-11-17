"""`CLI cleanup`: the worktrees the workflow left on disk, removed once their PR is done.

Ticket worktrees (`wt-<ID>-…`) and review worktrees (`wt-review-PR-<n>`) each keep gigabytes of build
outputs and were only ever removed by hand, where `git worktree remove` refuses a worktree with
submodules and deleting the folder lost the run state of PRs still open. This:

  * finds every worktree of the main checkout (never the main checkout itself);
  * asks GitHub for the state of its PR (by number for a review worktree, by branch otherwise);
  * removes it when the PR is merged or closed (`--all`: every one), after checking it has no
    uncommitted tracked change, with `--force` (submodules) and `git worktree prune`;
  * before removing a worktree whose PR is still open, copies its run folders to
    `MAIN/.ai/workflow/_archive/<run>/`, where `start` in a recreated worktree picks them up again;
  * deletes the local branch of a merged PR, and the review launcher's script and pid file;
  * `--base` also removes the base-build checkout the device stage compiles the base APK in.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from android_workflow.paths import WORKFLOW_ROOT, cache_dir

ARCHIVE_NAME = "_archive"
REVIEW_WORKTREE = re.compile(r"(?:^|/)wt-review-PR-(\d+)$")
SKIP_RUN_DIRS = {"_cache", "_setup", "_archive", "tmp"}
IN_USE_SECONDS = 15 * 60  # a run file touched this recently: a session is still working in the worktree


def _git(cwd: Path, *args: str, timeout: int = 600) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(cwd), *args], text=True, capture_output=True, check=False, timeout=timeout)


def main_checkout(target: Path) -> Path:
    common = _git(target, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()
    if not common:
        raise ValueError(f"{target} is not a git checkout")
    path = Path(common).resolve()
    return path.parent if path.name == ".git" else path


def worktrees(main: Path) -> list[dict[str, Any]]:
    """`git worktree list --porcelain` as dicts (path, head, branch), without the main checkout."""
    found: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for line in _git(main, "worktree", "list", "--porcelain").stdout.splitlines() + [""]:
        if not line.strip():
            if current:
                found.append(current)
            current = {}
        elif line.startswith("worktree "):
            current["path"] = Path(line[len("worktree "):]).resolve()
        elif line.startswith("branch "):
            current["branch"] = line[len("branch "):].removeprefix("refs/heads/")
        elif line.startswith("HEAD "):
            current["head"] = line[len("HEAD "):]
    return [item for item in found if item.get("path") and item["path"] != main.resolve()]


def _is_base_checkout(path: Path) -> bool:
    from android_workflow.base import state_home

    try:
        return path.resolve().is_relative_to((state_home() / "base").resolve())
    except (OSError, ValueError):
        return False


def pr_state(main: Path, env: dict[str, str], worktree: dict[str, Any]) -> dict[str, Any]:
    """`{number, state}` of the PR behind a worktree (MERGED, CLOSED, OPEN), or `{state: None}`."""
    from android_workflow.watch import _gh

    review = REVIEW_WORKTREE.search(str(worktree["path"]))
    if review:
        shown = _gh(["pr", "view", review.group(1), "--json", "number,state"], env=env, cwd=main)
        data = json.loads(shown.stdout) if shown.returncode == 0 and shown.stdout.strip() else {}
        return {"number": data.get("number") or int(review.group(1)), "state": data.get("state"), "review": True}
    branch = worktree.get("branch")
    if not branch:
        return {"state": None}
    listed = _gh(["pr", "list", "--head", branch, "--state", "all", "--json", "number,state", "--limit", "1"],
                 env=env, cwd=main)
    rows = json.loads(listed.stdout) if listed.returncode == 0 and listed.stdout.strip() else []
    return {"number": rows[0]["number"], "state": rows[0]["state"]} if rows else {"state": None}


def in_use(main: Path, path: Path, state: dict[str, Any]) -> str | None:
    """Why a session is still working in this worktree, or None."""
    import time

    if state.get("review") and state.get("number"):
        from android_workflow.review_inbox import session_alive

        try:
            if session_alive(main, int(state["number"])):
                return "its review session is still running"
        except Exception:  # noqa: BLE001 - no launcher record: fall back to the files
            pass
    if state.get("number") and not state.get("review"):
        from android_workflow.review_inbox import session_alive as alive

        try:
            if alive(path, int(state["number"]), "WATCH"):
                return "its PR Watcher session is still running"
        except Exception:  # noqa: BLE001
            pass
    root = path / WORKFLOW_ROOT
    newest = 0.0
    if root.is_dir():
        for run in root.iterdir():
            if run.is_dir() and run.name not in SKIP_RUN_DIRS:
                for name in ("run-state.json", "stage-log.md", "review-state.json", "device-report.md", "log.md"):
                    candidate = run / name
                    if candidate.is_file():
                        newest = max(newest, candidate.stat().st_mtime)
    if newest and time.time() - newest < IN_USE_SECONDS:
        return f"its run changed {int((time.time() - newest) // 60)} min ago (a session may still be working)"
    return None


def uncommitted(path: Path) -> list[str]:
    lines = _git(path, "status", "--porcelain", "--untracked-files=no").stdout.splitlines()
    return [line[3:] for line in lines if line.strip() and not line[3:].startswith(".ai/")]


def archive_runs(main: Path, path: Path) -> list[str]:
    """Copy the worktree's run folders into the main checkout's archive (newest copy wins)."""
    root = path / WORKFLOW_ROOT
    archived: list[str] = []
    if not root.is_dir():
        return archived
    for run in sorted(root.iterdir()):
        if not run.is_dir() or run.name in SKIP_RUN_DIRS or run.name.startswith("."):
            continue
        destination = main / WORKFLOW_ROOT / ARCHIVE_NAME / run.name
        if destination.exists():
            shutil.rmtree(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(run, destination, symlinks=True, ignore=shutil.ignore_patterns("media"))
        archived.append(run.name)
    return archived


def restore_run(target: Path, ticket_slug: str) -> bool:
    """`start` in a recreated worktree: bring back the run `cleanup` archived, when there is none here."""
    from android_workflow.paths import run_dir

    local = run_dir(target, ticket_slug)
    if local.exists():
        return False
    try:
        main = main_checkout(target)
    except ValueError:
        return False
    archived = main / WORKFLOW_ROOT / ARCHIVE_NAME / ticket_slug
    if not archived.is_dir() or main.resolve() == target.resolve():
        return False
    shutil.copytree(archived, local, symlinks=True)
    return True


def _forget_launch(main: Path, number: int | None) -> None:
    if not number:
        return
    folder = cache_dir(main) / "review-launch"
    for name in (f"PR-{number}.sh", f"PR-{number}.pid"):
        (folder / name).unlink(missing_ok=True)


def cleanup(target: Path, scope: str = "merged", base: bool = False, dry_run: bool = False,
            pr: int | None = None, force: bool = False) -> dict[str, Any]:
    """Remove the workflow's worktrees whose PR is done (`scope` merged), or all of them (`all`)."""
    from android_workflow.watch import github_env

    main = main_checkout(target.resolve())
    env: dict[str, str] | None = None
    removed, kept, archived, failed = [], [], {}, []
    for item in worktrees(main):
        path = item["path"]
        if _is_base_checkout(path):
            continue
        if env is None:
            env = github_env(main)
        state = pr_state(main, env, item)
        if pr is not None and state.get("number") != pr:
            continue
        done = state.get("state") in {"MERGED", "CLOSED"}
        if scope != "all" and not done:
            kept.append({"path": str(path), "pr": state.get("number"), "state": state.get("state") or "no PR"})
            continue
        busy = in_use(main, path, state) if path.is_dir() and not force else None
        if busy:
            kept.append({"path": str(path), "pr": state.get("number"), "state": state.get("state"), "reason": busy})
            continue
        dirty = uncommitted(path) if path.is_dir() else []
        if dirty:
            kept.append({"path": str(path), "pr": state.get("number"), "state": state.get("state"),
                         "reason": "uncommitted changes: " + ", ".join(dirty[:5])})
            continue
        entry = {"path": str(path), "pr": state.get("number"), "state": state.get("state") or "no PR",
                 "branch": item.get("branch")}
        if dry_run:
            removed.append({**entry, "dry_run": True})
            continue
        if not done and path.is_dir():
            saved = archive_runs(main, path)
            if saved:
                archived[str(path)] = saved
        gone = _git(main, "worktree", "remove", "--force", "--force", str(path))
        if gone.returncode != 0 and path.exists():
            failed.append({**entry, "error": (gone.stderr or gone.stdout).strip()[-300:]})
            continue
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
        if state.get("state") == "MERGED" and item.get("branch"):
            entry["branch_deleted"] = _git(main, "branch", "-D", item["branch"]).returncode == 0
        if state.get("review"):
            _forget_launch(main, state.get("number"))
        removed.append(entry)
    _git(main, "worktree", "prune")
    result: dict[str, Any] = {"main": str(main), "removed": removed, "kept": kept, "archived": archived,
                              "failed": failed}
    if base:
        from android_workflow.base import checkout_path, repository

        repo = repository(main)
        path = checkout_path(repo["id"]) if repo else None
        if path and path.exists():
            if not dry_run:
                _git(main, "worktree", "remove", "--force", "--force", str(path))
                shutil.rmtree(path, ignore_errors=True)
                _git(main, "worktree", "prune")
            result["base_checkout"] = {"path": str(path), "removed": not dry_run}
    return result
