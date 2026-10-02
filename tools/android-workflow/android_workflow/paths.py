"""Per-ticket run layout under <repo>/.ai/workflow/<ticket-id>/."""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

WORKFLOW_ROOT = ".ai/workflow"
CACHE_NAME = "_cache"
CURRENT_NAME = "current.json"

RESERVED_NAMES = {CACHE_NAME, CURRENT_NAME, "_setup", "preview"}
MEDIA_NAME = "media"
# The kit root (~/.ai): tools/android-workflow/android_workflow/paths.py → four levels up.
KIT_ROOT = Path(__file__).resolve().parents[3]
SKILL_DIR = KIT_ROOT / "skills" / "android-workflow"
MANIFEST_NAME = "manifest.json"
COMPARE_NAME = "compare.md"


def ticket_slug(ticket_id: str | None) -> str:
    raw = str(ticket_id or "LOCAL").strip() or "LOCAL"
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", raw)
    return slug[:80] or "LOCAL"


def workflow_root(target: Path) -> Path:
    return target / WORKFLOW_ROOT


def cache_dir(target: Path) -> Path:
    return workflow_root(target) / CACHE_NAME


def current_pointer(target: Path) -> Path:
    return workflow_root(target) / CURRENT_NAME


def set_current(target: Path, ticket_id: str, run_id: str | None = None) -> None:
    current_pointer(target).parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {"ticket_id": ticket_slug(ticket_id)}
    if run_id:
        payload["run_id"] = run_id
    pointer = current_pointer(target)
    temporary = pointer.with_name(f".{pointer.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, pointer)


def current_ticket_id(target: Path) -> str | None:
    path = current_pointer(target)
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(value, dict) and value.get("ticket_id"):
        return str(value["ticket_id"])
    return None


def run_dir(target: Path, ticket_id: str | None = None) -> Path:
    slug = ticket_slug(ticket_id) if ticket_id else current_ticket_id(target)
    if slug:
        return workflow_root(target) / slug
    raise FileNotFoundError("no active workflow run; call start first")


def media_dir(target: Path, ticket_id: str | None = None) -> Path:
    return run_dir(target, ticket_id) / MEDIA_NAME


LOCAL_ONLY_PATHS = (".ai/workflow/", ".ai/project-profile.md", ".ai/android-workflow.json")


def git_exclude_file(target: Path) -> Path | None:
    """`.git/info/exclude` (worktree-aware): ignores run files without dirtying a tracked file."""
    import subprocess

    def git(*args: str) -> str | None:
        result = subprocess.run(["git", "-C", str(target), *args], text=True, capture_output=True, check=False)
        return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None

    # Only when TARGET is its own repository root; a folder nested in another repo keeps .gitignore.
    top = git("rev-parse", "--show-toplevel")
    if not top or Path(top).resolve() != target.resolve():
        return None
    raw = git("rev-parse", "--git-path", "info/exclude")
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_absolute() else target / path


def ensure_gitignore(target: Path) -> None:
    exclude = git_exclude_file(target)
    gitignore = exclude or target / ".gitignore"
    existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    lines = existing.splitlines()
    wanted = list(LOCAL_ONLY_PATHS) if exclude else [".ai/workflow/"]
    gitignore.parent.mkdir(parents=True, exist_ok=True)
    missing = [line for line in wanted if line not in lines]
    if not missing:
        return
    suffix = ("\n" if existing and not existing.endswith("\n") else "") + "\n".join(missing) + "\n"
    gitignore.write_text(existing + suffix, encoding="utf-8")


def list_runs(target: Path) -> list[dict[str, Any]]:
    root = workflow_root(target)
    if not root.exists():
        return []
    current = current_ticket_id(target)
    runs: list[dict[str, Any]] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name in RESERVED_NAMES or child.name.startswith("_"):
            continue
        spec = child / "ticket-spec.json"
        state_path = child / "run-state.json"
        if not spec.exists() and not state_path.exists():
            continue
        status = None
        title = None
        if state_path.exists():
            status = json.loads(state_path.read_text(encoding="utf-8")).get("status")
        if spec.exists():
            ticket = json.loads(spec.read_text(encoding="utf-8")).get("ticket") or {}
            title = ticket.get("title")
        runs.append(
            {
                "ticket_id": child.name,
                "path": str(child),
                "status": status,
                "title": title,
                "current": child.name == current,
            }
        )
    return runs


STALE_STATUSES = frozenset({"awaiting_host", "paused", "running"})


def stale_runs(target: Path, hours: float) -> list[str]:
    """Runs left unfinished (never escalated or completed) with no event for `hours`."""
    cutoff = time.time() - hours * 3600
    stale = []
    for run in list_runs(target):
        state_path = Path(run["path"]) / "run-state.json"
        if run["status"] not in STALE_STATUSES or not state_path.exists():
            continue
        updated = json.loads(state_path.read_text(encoding="utf-8")).get("updated_at") or 0
        if updated < cutoff:
            stale.append(run["ticket_id"])
    return stale


def clean_runs(
    target: Path,
    ticket_id: str | None = None,
    stale_hours: float | None = None,
) -> dict[str, Any]:
    """One ticket's run, the unfinished runs idle for `stale_hours`, or — with neither — every run."""
    removed: list[str] = []
    if stale_hours is not None:
        for stale in stale_runs(target, stale_hours):
            removed.extend(clean_runs(target, ticket_id=stale)["removed"])
        return {"removed": removed}
    if not ticket_id:
        for run in list_runs(target):
            shutil.rmtree(run["path"], ignore_errors=True)
            removed.append(run["ticket_id"])
        cache = cache_dir(target)
        if cache.exists():
            shutil.rmtree(cache, ignore_errors=True)
            removed.append(CACHE_NAME)
        pointer = current_pointer(target)
        if pointer.exists():
            pointer.unlink()
            removed.append(CURRENT_NAME)
        return {"removed": removed}
    slug = ticket_slug(ticket_id)
    dest = workflow_root(target) / slug
    if dest.exists():
        shutil.rmtree(dest)
        removed.append(slug)
    if current_ticket_id(target) == slug:
        pointer = current_pointer(target)
        if pointer.exists():
            pointer.unlink()
        remaining = list_runs(target)
        if remaining:
            set_current(target, remaining[-1]["ticket_id"])
    return {"removed": removed}
