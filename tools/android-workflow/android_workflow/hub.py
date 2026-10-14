"""One office for every app and every run: the page a developer opens to watch any workflow.

Every command (and, while a run is live, the watcher) publishes its app's runs here, outside any
repository and outside the toolkit:

    ~/.local/state/android-workflow/office/
      office.html              the page, written once per toolkit version
      index.js / index.json    every app and run the office knows: the page's two selectors
      runs/<app>/<ticket>.js   one run's page data, loaded by the page when that run is picked

A local page cannot list folders, so the page loads `index.js` and the picked run's script with
`<script src>` and updates in place; no server. A run stays listed after its worktree is removed
(its media links then break) until `office --prune`; `clean` in an app drops its runs here too.
"""

from __future__ import annotations

import fcntl
import hashlib
import html
import json
import os
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from android_workflow.paths import current_ticket_id, list_runs, workflow_root

HOME_ENV = "ANDROID_WORKFLOW_OFFICE_HOME"
PAGE_NAME = "office.html"
INDEX_JSON = "index.json"
INDEX_JS = "index.js"
RUNS_NAME = "runs"


def hub_dir() -> Path:
    configured = os.environ.get(HOME_ENV)
    return Path(configured).expanduser() if configured else Path.home() / ".local" / "state" / "android-workflow" / "office"


def page_path() -> Path:
    return hub_dir() / PAGE_NAME


def app_id(target: Path) -> str:
    return hashlib.sha1(str(target.resolve()).encode("utf-8")).hexdigest()[:12]


def run_key(target: Path, ticket: str) -> str:
    return f"{app_id(target)}/{ticket}"


def run_src(key: str) -> str:
    return f"{RUNS_NAME}/{key}.js"


def _git(target: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(["git", "--no-optional-locks", "-C", str(target), *args],
                                text=True, capture_output=True, check=False, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None


def _app(target: Path) -> dict[str, Any]:
    """The app as the selectors show it: worktrees of one repository are one project."""
    target = target.resolve()
    common = _git(target, "rev-parse", "--git-common-dir")
    main = target
    if common:
        common_path = Path(common) if Path(common).is_absolute() else target / common
        if common_path.resolve().name == ".git":
            main = common_path.resolve().parent
    return {
        "id": app_id(target),
        "path": str(target),
        "project": hashlib.sha1(str(main).encode("utf-8")).hexdigest()[:12],
        "project_name": main.name,
        "worktree": None if main == target else target.name,
        "branch": _git(target, "branch", "--show-current"),
        "seen_at": time.time(),
    }


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def write_if_changed(path: Path, text: str) -> None:
    try:
        if path.read_text(encoding="utf-8") == text:
            return
    except OSError:
        pass
    _atomic_write(path, text)


@contextmanager
def _locked() -> Iterator[None]:
    """Several apps' commands and watchers update the one index: one at a time."""
    lock = hub_dir() / ".index.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def run_data(target: Path, ticket: str, full_scan: bool = False) -> dict[str, Any]:
    """One run's page data with links relative to the office folder; only the app's current run is live."""
    from android_workflow.office import collect

    key = run_key(target, ticket)
    current = ticket == current_ticket_id(target)
    data = collect(target, ticket, hub_dir(), live_src=run_src(key), full_scan=full_scan, snapshot=not current)
    data.update({"hub": True, "run_key": key})
    return data


def _summary(target: Path, data: dict[str, Any]) -> dict[str, Any]:
    state = _json(workflow_root(target) / str(data["ticket"]) / "run-state.json")
    desks = data.get("desks") or []
    return {
        "key": data["run_key"], "app": app_id(target), "ticket": data["ticket"], "title": data.get("title") or "",
        "status": data.get("status") or "idle", "live": bool(data.get("live")),
        "working": [desk["role"] for desk in desks if desk.get("state") == "working"],
        "question": bool(data.get("question")), "pr": data.get("pr"), "draft": bool(data.get("draft")),
        "created_at": data.get("created_at"), "updated_at": state.get("updated_at") or data.get("created_at") or 0,
        "src": run_src(data["run_key"]),
    }


def _payload(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/")


def write_run(data: dict[str, Any]) -> None:
    _atomic_write(hub_dir() / run_src(data["run_key"]), f"window.officeData && window.officeData({_payload(data)});\n")


def _stale(script: Path, run: Path) -> bool:
    if not script.exists():
        return True
    built = script.stat().st_mtime
    try:
        return any(item.stat().st_mtime > built for item in run.iterdir()
                   if item.is_file() and item.name != PAGE_NAME)  # the run's own page only points here
    except OSError:
        return False


def _still_live(app: dict[str, Any], run: dict[str, Any]) -> bool:
    """A run another app's watcher left `live` stops counting once that app moved on or it ended."""
    from android_workflow.office import run_is_live

    target = Path(app.get("path") or "")
    if not workflow_root(target).is_dir() or current_ticket_id(target) != run["ticket"]:
        return False
    return run_is_live(workflow_root(target) / run["ticket"])


def _write_index(index: dict[str, Any], skip_app: str | None = None) -> None:
    apps = index.get("apps") or {}
    for run in (index.get("runs") or {}).values():
        app = apps.get(run["app"]) or {}
        if run.get("live") and run["app"] != skip_app:
            run["live"] = _still_live(app, run)
    for app in apps.values():
        app["gone"] = not Path(app.get("path") or "").is_dir()
    write_if_changed(hub_dir() / INDEX_JSON, json.dumps(index, indent=1, ensure_ascii=False) + "\n")
    runs = sorted((index.get("runs") or {}).values(), key=lambda run: (not run.get("live"), -(run.get("updated_at") or 0)))
    projects: dict[str, dict[str, Any]] = {}
    for run in runs:
        app = apps.get(run["app"]) or {}
        project = projects.setdefault(app.get("project") or run["app"], {
            "id": app.get("project") or run["app"], "name": app.get("project_name") or "unknown", "runs": 0, "live": 0})
        project["runs"] += 1
        project["live"] += 1 if run.get("live") else 0
    page = {
        "generated_at": time.time(),
        "projects": sorted(projects.values(), key=lambda project: project["name"].lower()),
        "runs": [{**run, "project": (apps.get(run["app"]) or {}).get("project") or run["app"],
                  "worktree": (apps.get(run["app"]) or {}).get("worktree"),
                  "branch": (apps.get(run["app"]) or {}).get("branch"),
                  "gone": bool((apps.get(run["app"]) or {}).get("gone"))} for run in runs],
    }
    write_if_changed(hub_dir() / INDEX_JS, f"window.officeIndex && window.officeIndex({_payload(page)});\n")


def publish_page() -> None:
    from android_workflow.office import PAGE, SIM_JS

    empty = {"hub": True, "index_src": INDEX_JS, "ticket": None, "status": "idle", "desks": [], "timeline": [],
             "media": {"before": [], "after": []}, "artifacts": {}, "logs": [], "files": [], "draft": [],
             "totals": {}, "changes": [], "live": False, "live_src": None}
    write_if_changed(page_path(), PAGE.replace("__SIM__", SIM_JS).replace("__DATA__", _payload(empty)))


def publish(target: Path, live: dict[str, Any] | None = None) -> None:
    """Bring the office up to date with TARGET's runs.

    With `live` (the watcher's tick: the current run's fresh data), only that run is rewritten.
    Otherwise every run of the app is checked: the current one and changed ones are rewritten, and
    runs `clean` removed leave the office.
    """
    target = target.resolve()
    me = app_id(target)
    if live is not None:
        write_run(live)
        with _locked():
            index = _json(hub_dir() / INDEX_JSON)
            apps = index.setdefault("apps", {})
            if me not in apps:
                apps[me] = _app(target)
            index.setdefault("runs", {})[live["run_key"]] = _summary(target, live)
            _write_index(index, skip_app=me)
        return
    publish_page()
    current = current_ticket_id(target)
    tickets = [item["ticket_id"] for item in list_runs(target)]
    with _locked():
        index = _json(hub_dir() / INDEX_JSON)
        apps = index.setdefault("apps", {})
        runs = index.setdefault("runs", {})
        known = {key for key, run in runs.items() if run.get("app") == me}
        for key in known - {run_key(target, ticket) for ticket in tickets}:
            runs.pop(key, None)
            (hub_dir() / run_src(key)).unlink(missing_ok=True)
        if tickets:
            apps[me] = _app(target)
        else:
            apps.pop(me, None)
        _write_index(index, skip_app=me)
    summaries = {}
    for ticket in tickets:
        key = run_key(target, ticket)
        script = hub_dir() / run_src(key)
        if ticket != current and key in known and not _stale(script, workflow_root(target) / ticket):
            continue
        data = run_data(target, ticket)
        write_run(data)
        summaries[key] = _summary(target, data)
    if not summaries:
        return
    with _locked():
        index = _json(hub_dir() / INDEX_JSON)
        if me in (index.get("apps") or {}):
            index.setdefault("runs", {}).update(summaries)
            _write_index(index, skip_app=me)


def prune(days: float | None = None) -> dict[str, Any]:
    """Drop runs whose app folder or run folder is gone and, with `days`, past runs idle that long."""
    removed: list[str] = []
    cutoff = time.time() - days * 86400 if days is not None else None
    with _locked():
        index = _json(hub_dir() / INDEX_JSON)
        apps = index.setdefault("apps", {})
        runs = index.setdefault("runs", {})
        for key, run in list(runs.items()):
            app = apps.get(run.get("app")) or {}
            folder = workflow_root(Path(app.get("path") or "/nonexistent")) / str(run.get("ticket"))
            old = cutoff is not None and not run.get("live") and (run.get("updated_at") or 0) < cutoff
            if old or not folder.is_dir():
                runs.pop(key)
                (hub_dir() / run_src(key)).unlink(missing_ok=True)
                removed.append(key)
        for app_key in [key for key in apps if not any(run.get("app") == key for run in runs.values())]:
            apps.pop(app_key)
            folder = hub_dir() / RUNS_NAME / app_key
            if folder.is_dir() and not any(folder.iterdir()):
                folder.rmdir()
        _write_index(index)
    publish_page()
    return {"removed": removed, "kept": len(runs)}


def stub(target: Path, ticket: str | None) -> str:
    """The app's own `office.html`: it opens the one office at this app's current run."""
    hub = page_path().resolve().as_uri()
    fragment = f"#run={run_key(target, ticket)}" if ticket else ""
    url = _payload(hub + fragment)
    return (
        "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\"><title>Agent Office</title>\n"
        f"<script>location.replace({url});</script></head>\n"
        f"<body><p><a href=\"{html.escape(hub + fragment)}\">Open the Agent Office</a></p></body></html>\n"
    )
