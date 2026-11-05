"""Worktrees build from the main checkout's Gradle build cache.

A project can keep its local build cache inside the checkout (`buildCache { local { directory =
new File(rootDir, 'gradle-build-cache') } }`). Then every new worktree (a review, the base checkout)
starts with an empty cache and builds the whole app from scratch, while the main checkout's cache
already holds almost every task output. Each Gradle run the workflow starts in a worktree gets an
init script that, after the settings ran, points that checkout-local cache at the same folder in
the main checkout. Gradle locks its cache across processes, so several builds can share it. A cache
outside the checkout (the default `~/.gradle/caches/build-cache-1`) is already shared and stays as is.

`ANDROID_WORKFLOW_SHARED_BUILD_CACHE=0` turns it off.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ENV = "ANDROID_WORKFLOW_SHARED_BUILD_CACHE"
ROOT_ENV = "AW_SHARED_BUILD_CACHE_ROOT"
GRADLE_EXECUTABLES = frozenset({"gradlew", "gradle", "gradlew.bat", "gradle.bat"})
INIT_SCRIPT = """\
// android-workflow: a worktree uses the main checkout's checkout-local Gradle build cache.
def sharedRoot = System.getenv("AW_SHARED_BUILD_CACHE_ROOT")
if (sharedRoot) {
    gradle.settingsEvaluated { settings ->
        def local = settings.buildCache.local
        def configured = local.directory
        if (configured == null) {
            return
        }
        def root = settings.rootDir.canonicalFile
        def dir = configured instanceof File ? configured : new File(configured.toString())
        if (!dir.absolute) {
            dir = new File(root, dir.path)
        }
        def path = dir.canonicalPath
        if (path.startsWith(root.path + File.separator)) {
            local.directory = new File(sharedRoot, path.substring(root.path.length() + 1))
        }
    }
}
"""


def enabled() -> bool:
    return os.environ.get(ENV, "").strip().lower() not in {"0", "false", "no", "off"}


def init_script() -> Path:
    """The init script, written once under the workflow's cache (rewritten only when it changes)."""
    path = Path.home() / ".cache" / "android-workflow" / "shared-build-cache.gradle"
    try:
        if not path.is_file() or path.read_text(encoding="utf-8") != INIT_SCRIPT:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(INIT_SCRIPT, encoding="utf-8")
    except OSError:
        pass
    return path


def main_checkout(cwd: Path) -> Path | None:
    """The repository's main checkout when `cwd` is another worktree of it, else None."""
    try:
        found = subprocess.run(["git", "-C", str(cwd), "rev-parse", "--path-format=absolute", "--git-common-dir",
                                "--show-toplevel"], text=True, capture_output=True, check=False, timeout=30,
                               stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    lines = found.stdout.strip().splitlines()
    if found.returncode != 0 or len(lines) < 2:
        return None
    common, top = Path(lines[0]).resolve(), Path(lines[1]).resolve()
    if common.name != ".git":
        return None  # a bare repository has no main checkout to share
    main = common.parent
    return main if main != top and main.is_dir() else None


def is_gradle(parts: list[str]) -> bool:
    return bool(parts) and Path(parts[0]).name in GRADLE_EXECUTABLES


def wrap(parts: list[str], cwd: Path, env: dict[str, str] | None = None) -> tuple[list[str], dict[str, str] | None]:
    """`(command, env)` for a Gradle run in `cwd`: with the init script and the main checkout's root when
    `cwd` is a worktree. Anything else comes back unchanged."""
    if not enabled() or not is_gradle(parts):
        return parts, env
    main = main_checkout(cwd)
    if main is None:
        return parts, env
    script = init_script()
    if not script.is_file():
        return parts, env
    return [parts[0], "--init-script", str(script), *parts[1:]], {**(env if env is not None else os.environ),
                                                                   ROOT_ENV: str(main)}
