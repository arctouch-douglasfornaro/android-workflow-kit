"""The tools every CLI run needs on PATH (`gh`) and a JDK for Gradle, whatever shell started it.

A session opened by a launcher (`zsh -l`, Terminal One, cron) never reads `.zshrc`, so `gh` in
`~/.local/bin` and the `JAVA_HOME` set there are missing, and `./gradlew` stops with "Unable to locate
a Java Runtime". `prepare()` runs once per CLI call: it adds the usual tool folders that exist to PATH
and, when `JAVA_HOME` is unset or points at a removed JDK, picks one Gradle can start with.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

EXTRA_PATH_DIRS = ("~/.local/bin", "/opt/homebrew/bin", "/usr/local/bin")
DAEMON_JVM_FILE = Path("gradle") / "gradle-daemon-jvm.properties"


def extra_path_dirs() -> list[str]:
    return [str(Path(entry).expanduser()) for entry in EXTRA_PATH_DIRS]


def with_tool_dirs(path: str) -> str:
    """PATH with every existing tool folder it lacks appended (the caller's order wins)."""
    parts = [part for part in path.split(os.pathsep) if part]
    for entry in extra_path_dirs():
        if entry not in parts and Path(entry).is_dir():
            parts.append(entry)
    return os.pathsep.join(parts)


def find_gh(path: str | None = None) -> str | None:
    return shutil.which("gh", path=with_tool_dirs(path if path is not None else os.environ.get("PATH", "")))


def java_ok(home: str | Path | None) -> bool:
    if not home:
        return False
    java = Path(home) / "bin" / "java"
    return java.is_file() and os.access(java, os.X_OK)


def _major(home: Path) -> int | None:
    try:
        text = (home / "release").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    found = re.search(r'^JAVA_VERSION="(?:1\.)?(\d+)', text, re.M)
    return int(found.group(1)) if found else None


def _property(file: Path, key: str) -> str | None:
    try:
        text = file.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    found = re.search(rf"^\s*{re.escape(key)}\s*=\s*(.+?)\s*$", text, re.M)
    return found.group(1).replace("\\:", ":") if found else None


def _homes_under(folder: Path) -> list[Path]:
    """JDK homes inside a folder of JDKs (`~/.gradle/jdks`, `JavaVirtualMachines`), any layout depth."""
    homes: list[Path] = []
    if not folder.is_dir():
        return homes
    for child in sorted(folder.iterdir()):
        if not child.is_dir():
            continue
        for home in (child, child / "Contents" / "Home", *child.glob("*/Contents/Home"), *child.glob("*/")):
            if java_ok(home) and home not in homes:
                homes.append(home)
                break
    return homes


def system_homes() -> list[Path]:
    """Android Studio's bundled JDK, Homebrew's and the macOS JavaVirtualMachines folders."""
    homes: list[Path] = []
    for apps in (Path("/Applications"), Path.home() / "Applications"):
        homes += [app / "Contents" / "jbr" / "Contents" / "Home" for app in sorted(apps.glob("Android Studio*.app"))]
    homes += sorted(Path("/opt/homebrew/opt").glob("openjdk*/libexec/openjdk.jdk/Contents/Home"))
    homes += _homes_under(Path.home() / "Library" / "Java" / "JavaVirtualMachines")
    homes += _homes_under(Path("/Library/Java/JavaVirtualMachines"))
    return homes


def jdk_candidates(target: Path | None = None) -> list[Path]:
    """JDK homes in order of preference: the one Gradle is told to use, then the installed ones."""
    found: list[Path] = []
    for file in ([target / "gradle.properties"] if target else []) + [Path.home() / ".gradle" / "gradle.properties"]:
        value = _property(file, "org.gradle.java.home")
        if value:
            found.append(Path(value).expanduser())
    installed = _homes_under(Path.home() / ".gradle" / "jdks") + system_homes()
    wanted = None
    if target is not None:
        version = _property(target / DAEMON_JVM_FILE, "toolchainVersion")
        wanted = int(version) if version and version.isdigit() else None
    if wanted is not None:
        installed.sort(key=lambda home: _major(home) != wanted)  # stable: the wanted version first
    return [home for home in dict.fromkeys(found + installed) if java_ok(home)]


def find_java_home(target: Path | None = None, current: str | None = None) -> str | None:
    if java_ok(current):
        return current
    for home in jdk_candidates(target):
        return str(home)
    try:
        probed = subprocess.run(["/usr/libexec/java_home"], text=True, capture_output=True, check=False, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    home = probed.stdout.strip()
    return home if probed.returncode == 0 and java_ok(home) else None


def prepare(target: Path | None = None, environ: Any = None) -> dict[str, Any]:
    """Fix PATH and JAVA_HOME in `environ` (os.environ by default) in place; what changed, for logs."""
    env = os.environ if environ is None else environ
    changed: dict[str, Any] = {}
    path = env.get("PATH", "")
    fuller = with_tool_dirs(path)
    if fuller != path:
        env["PATH"] = fuller
        changed["path_added"] = [part for part in fuller.split(os.pathsep) if part not in path.split(os.pathsep)]
    current = env.get("JAVA_HOME")
    if not java_ok(current) and not (current is None and shutil.which("java", path=env.get("PATH")) and _java_runs(env)):
        home = find_java_home(target, None)
        if home:
            env["JAVA_HOME"] = home
            env["PATH"] = os.pathsep.join([str(Path(home) / "bin"), env.get("PATH", "")])
            changed["java_home"] = home
            if current:
                changed["java_home_was"] = current
    return changed


def _java_runs(env: Any) -> bool:
    """`java` on PATH can be the macOS stub that only says no runtime is installed."""
    try:
        probe = subprocess.run(["java", "-version"], text=True, capture_output=True, check=False, timeout=15,
                               env=dict(env))
    except (OSError, subprocess.SubprocessError):
        return False
    return probe.returncode == 0
