#!/usr/bin/env python3
"""Persist first-run profile facts without Gradle, network, or human prompts."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

PRUNE = {".git", ".gradle", ".idea", "build", "out", "node_modules", ".kotlin"}
CONFIG_NAMES = {
    "AGENTS.md", "settings.gradle", "settings.gradle.kts", "build.gradle",
    "build.gradle.kts", "gradle.properties", "gradle-wrapper.properties",
    ".editorconfig", "gradlew", "AndroidManifest.xml",
}


VERSION_ONLY_COORDINATE = re.compile(r'("[\w.\-]+:[\w.\-]+):[^"$:\s]+(?=")')
VERSION_ONLY_ASSIGNMENT = re.compile(r'\bversion(\s*=\s*|\s+)"[^"$]*"')
WRAPPER_VERSION = re.compile(r"gradle-[\d.]+(?:-\w+)?-(?:bin|all)")
TOML_SECTION = re.compile(r"^\s*\[([^\]]+)\]\s*$")
TOML_VALUE = re.compile(r'^(\s*[\w.\-]+\s*=\s*)"[^"]*"(.*)$')


def structural_text(name: str, data: bytes) -> bytes:
    """Build files with dependency versions blanked, so a version bump is not a structure change.

    Plugins, modules, tasks, aliases and conventions stay in the text. Only literal versions go:
    catalog `[versions]` values, `version = "x"`, `group:name:x` coordinates and the wrapper's
    distribution version.
    """
    if not (name.endswith((".toml", ".gradle", ".gradle.kts")) or name == "gradle-wrapper.properties"):
        return data
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    if name == "gradle-wrapper.properties":
        return WRAPPER_VERSION.sub("gradle-VERSION", text).encode()
    lines = []
    section = ""
    for line in text.splitlines():
        header = TOML_SECTION.match(line) if name.endswith(".toml") else None
        if header:
            section = header.group(1).strip()
        elif name.endswith(".toml") and section == "versions":
            line = TOML_VALUE.sub(r'\1""\2', line)
        line = VERSION_ONLY_ASSIGNMENT.sub(r'version\1""', line)
        line = VERSION_ONLY_COORDINATE.sub(r"\1", line)
        lines.append(line)
    return "\n".join(lines).encode()


def digests(root: Path) -> tuple[str, str]:
    """`(exact, structural)` digests of the build configuration.

    Content, not checkout timestamps; include module and convention-plugin configuration. The
    structural one ignores dependency version bumps only.
    """
    root = root.resolve()
    exact = hashlib.sha256()
    structural = hashlib.sha256()
    convention_roots = []
    for settings in (root / "settings.gradle", root / "settings.gradle.kts"):
        if settings.is_file():
            for relative in re.findall(r"""includeBuild\s*\(?\s*["']([^"']+)["']""",
                                       settings.read_text()):
                candidate = (root / relative).resolve()
                if candidate.is_relative_to(root):
                    convention_roots.append(candidate)
    for directory, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in PRUNE
                         and not Path(directory, d).is_symlink()
                         and Path(directory, d).relative_to(root).as_posix() != ".ai/workflow")
        for name in sorted(files):
            path = Path(directory, name)
            rel = path.relative_to(root).as_posix()
            relevant = (name in CONFIG_NAMES or name.endswith((".gradle", ".gradle.kts", ".toml"))
                        or rel.startswith(("buildSrc/src/", "build-logic/", "gradle-common/",
                                           ".ai/rules/", ".ai/skills/"))
                        or ("/src/" in rel and name.endswith((".kt", ".java"))
                            and "gradle" in rel.lower())
                        or (name.endswith((".kt", ".java", ".groovy", ".properties"))
                            and any(path.is_relative_to(p) for p in convention_roots)))
            if relevant:
                content = path.read_bytes() if not path.is_symlink() else os.readlink(path).encode()
                for hasher, data in ((exact, content), (structural, structural_text(name, content))):
                    hasher.update(rel.encode() + b"\0")
                    hasher.update(data)
                    hasher.update(b"\0")
    return exact.hexdigest(), structural.hexdigest()


def configuration_digest(root: Path) -> str:
    return digests(root)[0]


EXEMPLAR = re.compile(r"`([\w./\-]+\.(?:kt|kts|java|xml|gradle|md)):(\d+)`")


def missing_exemplars(profile_text: str, root: Path) -> list[str]:
    """Files the profile cites as `path:line` that no longer exist; a moved line is not missing."""
    return sorted({path for path, _ in EXEMPLAR.findall(profile_text) if not (root / path).is_file()})


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".setup-")
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(text)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def metadata_path(root: Path) -> Path:
    return root / ".ai/workflow/_setup/profile-state.json"


def load_metadata(root: Path) -> dict:
    path = metadata_path(root)
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def minimal_profile(root: Path) -> str:
    settings = next((n for n in ("settings.gradle.kts", "settings.gradle")
                     if (root / n).is_file()), "")
    wrapper = "./gradlew" if (root / "gradlew").is_file() else ""
    return f"""# Project profile

Generated automatically on first use. Empty cells are unknown, not defaults.
This local profile is excluded from feature delivery. Preserve human-owned rows.

## Provenance
| What | Value |
|---|---|
| Generated by | feature_setup.py; partial until project-explorer completes required facts |
| Configuration tracking | .ai/workflow/_setup/profile-state.json (content fingerprint) |

## Layout
| What | Value |
|---|---|
| Settings file | {settings} |
| Gradle wrapper | {wrapper} |
| Android Gradle project | |
| App module(s) | |
| UI toolkit | |
| DI | |

## Build commands
Record exact tasks per module/variant, with source evidence; do not infer JVM from absence
of an Android plugin literal. Follow convention plugins and version-catalog aliases.

| Module | Plugin / variant | Compile | Unit tests | Lint | Evidence |
|---|---|---|---|---|---|

## Lint engine
| What | Value |
|---|---|
| Engine | |
| Check task | |
| Format task | |

## Tests
| What | Value |
|---|---|
| Libraries | |
| Screenshot engine / task | |
| At-risk consumers | |

## Device
| What | Value |
|---|---|
| Debug package / launcher | |
| Existing AVD authorized for automatic start | |
| Reusable flow / entry point | |
| Event-log observation | |
| APK build / install commands | |

## Feature overview docs
| What | Value |
|---|---|
| Glob | |

## Environment repairs
Record verified, authorized repairs only. No credentials.

## Code patterns
One row per area with an exemplar the planner and reviewer can cite. <=25 rows.

| Area | Pattern used here | Exemplar (path:line) |
|---|---|---|

## Project conventions that are blocking, not stylistic
| Convention | Check / evidence |
|---|---|

## Test data
No invented accounts, credentials, or server state.

## Extra convention sources
| Topic | Path |
|---|---|
| Repository rules | {"AGENTS.md" if (root / "AGENTS.md").is_file() else ""} |
"""


def ensure(root: Path, source: Path | None = None, no_probe: bool = False,
           accept: bool = False, force: bool = False) -> dict:
    root = root.resolve()
    if not any((root / n).is_file() for n in ("settings.gradle", "settings.gradle.kts")):
        raise ValueError("No Gradle settings file; refusing to create an Android profile")
    profile = root / ".ai/project-profile.md"
    fingerprint, structure = digests(root)
    state = load_metadata(root)
    copied = False
    refreshed = False
    untouched_minimal = (
        profile.exists() and not state.get("complete") and profile.read_text() == minimal_profile(root)
    )
    if (not profile.exists() or untouched_minimal) and source:
        source = source.resolve()
        source_state = load_metadata(source)
        source_profile = source / ".ai/project-profile.md"
        if source_profile.is_file() and source_state.get("complete"):
            source_exact, source_structure = digests(source)
            same_configuration = source_state.get("configuration") == fingerprint and source_exact == fingerprint
            same_structure = (
                source_state.get("configuration") == source_exact
                and source_structure == structure
                and not missing_exemplars(source_profile.read_text(), root)
            )
            if same_configuration or same_structure:
                atomic_write(profile, source_profile.read_text())
                state = {**source_state, "configuration": fingerprint, "structure": structure}
                copied = True
    created = not profile.exists()
    if created:
        atomic_write(profile, minimal_profile(root))
    if created:
        state = {}
    profile_digest = hashlib.sha256(profile.read_bytes()).hexdigest()
    if state.get("profile_digest") != profile_digest and not copied:
        state["complete"] = False
    if (
        not created and not copied and not force
        and state.get("complete") and state.get("configuration") != fingerprint
        and state.get("structure") == structure
        and not missing_exemplars(profile.read_text(), root)
    ):
        # Only dependency versions moved: the profile still describes this build, no agent needed.
        state["configuration"] = fingerprint
        refreshed = True
    if accept:
        if created or not profile.read_text().strip():
            raise ValueError("Complete the persisted profile before accepting it")
        if state.get("configuration") != fingerprint:
            raise ValueError("Configuration changed; run --ensure before accepting the profile")
        state["complete"] = True
    fresh = state.get("configuration") == fingerprint and state.get("complete", False)
    needs_explorer = force or not fresh
    probe_path = root / ".ai/workflow/_setup/probe.md"
    if needs_explorer and not no_probe and not accept:
        result = subprocess.run(
            [sys.executable, str(Path(__file__).with_name("android_probe.py")),
             "--repo-root", str(root)], capture_output=True, text=True, check=True,
        )
        atomic_write(probe_path, result.stdout)
    next_state = {
        "configuration": fingerprint,
        "structure": structure,
        "complete": bool(accept or (fresh and not force)),
        "profile": str(profile),
        "profile_digest": profile_digest,
    }
    atomic_write(metadata_path(root), json.dumps(next_state, indent=2) + "\n")
    return {
        "profile": str(profile), "created": created, "copied_from_worktree_source": copied,
        "refreshed_without_agent": refreshed,
        "requires_explorer": not next_state["complete"],
        "probe": str(probe_path) if probe_path.exists() else None,
        "status": "ready" if next_state["complete"] else "partial",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--source-repo", type=Path)
    parser.add_argument("--ensure", action="store_true")
    parser.add_argument("--no-probe", action="store_true")
    parser.add_argument("--accept", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(ensure(args.repo_root, args.source_repo, args.no_probe,
                                args.accept, args.force)))
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
