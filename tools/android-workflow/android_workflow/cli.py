from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from android_workflow.paths import (
    SKILL_DIR,
    cache_dir,
    clean_runs,
    ensure_gitignore,
    list_runs,
    run_dir,
    set_current,
    ticket_slug,
)

SCHEMA_VERSION = 1
STAGES = [f"T{i}" for i in range(10)]
STAGE_ROLES = {
    "T0": "Bootstrap",
    "T1": "Triage",
    "T2": "Localizer",
    "T3": "Planner",
    "T4": "Implementer",
    "T4L": "Tech Lead",
    "T5": "Quality gate",
    "T6": "Reviewer",
    "T7": "Device",
    "T8": "Delivery",
    "T9": "Telemetry",
}


# Agents that work inside a stage; the log shows the agent that actually ran.
AGENT_STAGES = {
    "setup": "T0", "planner": "T3", "implementer": "T4", "quality gate": "T5",
    "gate": "T5", "reviewer": "T6", "device": "T7", "delivery": "T8", "tech lead": "T4L",
}


def stage_label(stage: str) -> str:
    """People read role names; T-codes stay inside the run files."""
    return STAGE_ROLES.get(stage, stage)


def resolve_stage(value: str) -> tuple[str, str]:
    """`Implementer`, `aw-implementer`, `quality-gate` or `T4` → (stage id, role name to show)."""
    text = value.strip()
    if text.upper() in STAGE_ROLES:
        return text.upper(), STAGE_ROLES[text.upper()]
    key = re.sub(r"[-_\s]+", " ", text.lower())
    key = key[3:] if key.startswith("aw ") else key
    roles = {name.lower(): stage for stage, name in STAGE_ROLES.items()}
    stage = AGENT_STAGES.get(key) or roles.get(key)
    if not stage:
        names = ", ".join(dict.fromkeys(name.title() for name in AGENT_STAGES if name != "gate"))
        raise ValueError(f"unknown stage {value!r}; use a role name: {names}")
    display = key.title() if key in AGENT_STAGES and key not in {"gate", "quality gate"} else STAGE_ROLES[stage]
    return stage, display


def role_view(value: Any) -> Any:
    """CLI output with role names in place of stage ids (`stages`, `current_stage`)."""
    if isinstance(value, list):
        return [role_view(item) for item in value]
    if not isinstance(value, dict):
        return value
    view: dict[str, Any] = {}
    for key, item in value.items():
        if key == "stages" and isinstance(item, dict):
            view[key] = {STAGE_ROLES.get(stage, stage): role_view(entry) for stage, entry in item.items()}
        elif key == "current_stage" and isinstance(item, str):
            view[key] = STAGE_ROLES.get(item, item)
        else:
            view[key] = role_view(item)
    return view


DEFAULT_CONFIG: dict[str, Any] = {
    "tools": {"gradle": "./gradlew", "adb": "adb"},
    "commands": {
        "build": "./gradlew assembleDebug",
        "unit_tests": "./gradlew testDebugUnitTest",
        "android_lint": "./gradlew lintDebug",
        "ktlint": None,
        "format_check": None,
        "format_apply": None,
        "detekt": None,
        "install": "./gradlew installDebug",
    },
    "device": {"app_module": None, "application_id": None, "launch_activity": None},
    # Pure Kotlin/Java modules have no variants: `:data:compileKotlin`, not `compileDebugKotlin`.
    "jvm_modules": None,  # None = detect; a list recorded by Setup wins
    "jvm_module_commands": {
        "compile": "./gradlew {module}:compileKotlin",
        "unit_tests": "./gradlew {module}:test",
        "android_lint": None,
    },
    "module_commands": {
        "compile": "./gradlew {module}:compileDebugKotlin",
        "unit_tests": "./gradlew {module}:testDebugUnitTest",
        "android_lint": "./gradlew {module}:lintDebug",
    },
    "quality_gates": {
        # One Gradle invocation with --continue instead of one per task.
        "batch_gradle": True,
        # Downstream modules that reference a changed declaration get compile + unit tests.
        "max_consumer_modules": 12,
    },
    "execution": {"command_timeout_seconds": 900},
    "overrides": {},
}


def read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    """Atomic: the office watcher reads these files while commands write them."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def text_if_exists(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, UnicodeDecodeError, OSError):
        return ""


TOOLKIT_SOURCES = (
    (Path(__file__).resolve().parent, "*.py"),
    (Path.home() / ".ai" / "skills" / "android-workflow", "*"),
    (Path.home() / ".ai" / "bin", "feature_setup.py"),
    (Path.home() / ".ai" / "bin", "feature_workspace.py"),
    (Path.home() / ".ai" / "bin", "android-workflow"),
)


def toolkit_fingerprint() -> dict[str, str]:
    """Hash of the code and instructions that judge a run, so a run cannot quietly rewrite its own gate."""
    files: dict[str, str] = {}
    for root, pattern in TOOLKIT_SOURCES:
        if not root.is_dir():
            continue
        paths = root.rglob(pattern) if pattern == "*" else root.glob(pattern)
        for path in paths:
            if path.is_file() and "__pycache__" not in path.parts and not path.name.endswith("_test.py"):
                files[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return files


TOOLKIT_FINGERPRINT_FILE = "toolkit-fingerprint.json"


def toolkit_changes(agent_dir: Path) -> list[str]:
    path = agent_dir / TOOLKIT_FINGERPRINT_FILE
    recorded = read_json(path).get("files") if path.exists() else None
    if not recorded:
        return []
    current = toolkit_fingerprint()
    return sorted(path for path in set(recorded) | set(current) if recorded.get(path) != current.get(path))


PRUNE_DIRS = frozenset({
    ".git", "build", ".gradle", ".ai", ".idea", ".kotlin", "node_modules", "out",
    ".cxx", "generated", "intermediates",
})


class RepoScan:
    """One pruned walk of the repository shared by every detector.

    `rglob` over a real app also walks build/, .gradle/ and node_modules; on a large project
    that took minutes per `start`. Pruning while walking keeps it to seconds.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.dir_names: set[str] = set()
        self.gradle_files: list[Path] = []
        self.kotlin_files: list[Path] = []
        self.java_files: list[Path] = []
        for current, dirs, files in os.walk(root):
            dirs[:] = sorted(name for name in dirs if name not in PRUNE_DIRS)
            self.dir_names.update(name.lower() for name in dirs)
            for name in files:
                path = Path(current) / name
                if name.endswith((".gradle", ".gradle.kts", ".toml")):
                    self.gradle_files.append(path)
                elif name.endswith(".kt"):
                    self.kotlin_files.append(path)
                elif name.endswith(".java"):
                    self.java_files.append(path)
        self._gradle_text: str | None = None

    @property
    def gradle_text(self) -> str:
        if self._gradle_text is None:
            self._gradle_text = "\n".join(text_if_exists(path) for path in self.gradle_files)
        return self._gradle_text


def all_gradle_text(root: Path, scan: RepoScan | None = None) -> str:
    return (scan or RepoScan(root)).gradle_text


def first_match(patterns: list[str], text: str) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text, re.MULTILINE)
        if match:
            return match.group(1)
    return None


def detect_modules(root: Path) -> list[str]:
    settings = text_if_exists(root / "settings.gradle.kts") or text_if_exists(root / "settings.gradle")
    modules: set[str] = set()
    for line in settings.splitlines():
        if "include" not in line or line.lstrip().startswith("//"):
            continue
        modules.update(re.findall(r"""['"](:[^'"]+)['"]""", line))
    if not modules and ((root / "app" / "build.gradle").exists() or (root / "app" / "build.gradle.kts").exists()):
        modules.add(":app")
    return sorted(modules)


def module_dir(root: Path, module: str) -> Path:
    return root.joinpath(*module.strip(":").split(":"))


def resolve_constant(value: str | None, root: Path, text: str) -> str | None:
    """`compileSdk = COMPILE_SDK_VERSION` -> the number from gradle.properties or an `ext` value."""
    if not value or value.isdigit():
        return value
    name = value.split(".")[-1]
    sources = text_if_exists(root / "gradle.properties") + "\n" + text
    quoted = re.escape(name)
    return first_match(
        [
            rf"""^\s*(?:ext\.)?{quoted}\s*[=:]\s*['"]?(\d+)""",  # gradle.properties / Groovy ext
            rf"""extra\[\s*["']{quoted}["']\s*\]\s*=\s*(\d+)""",  # Kotlin DSL extra["NAME"] = 37
            rf"""(?:val|const val)\s+{quoted}\s*(?::\s*Int)?\s*=\s*(\d+)""",  # buildSrc constant
        ],
        sources,
    )


def detect_versions(root: Path, scan: RepoScan | None = None) -> dict[str, str | None]:
    text = all_gradle_text(root, scan)
    sdk = {
        key: resolve_constant(
            first_match([rf"{key}(?:Version)?\s*[=( ]\s*([A-Za-z_][\w.]*|\d+)"], text), root, text
        )
        for key in ("compileSdk", "minSdk", "targetSdk")
    }
    wrapper = text_if_exists(root / "gradle/wrapper/gradle-wrapper.properties")
    return {
        "gradle": first_match([r"gradle-([0-9][0-9A-Za-z.+-]*)-(?:bin|all)\.zip"], wrapper),
        "agp": first_match(
            [
                r"""com\.android\.(?:application|library)['"]?\)?\s+version\s+['"]([^'"]+)""",
                r"""^\s*(?:androidGradlePlugin|android-gradle-plugin|agp)[\w-]*\s*=\s*['"]([^'"]+)""",
            ],
            text,
        ),
        "kotlin": first_match(
            [
                r"""org\.jetbrains\.kotlin\.[\w.]+['"]?\)?\s+version\s+['"]([^'"]+)""",
                r"""^\s*kotlin(?:Version|-version|_version)?\s*=\s*['"]([^'"]+)""",
            ],
            text,
        ),
        "compile_sdk": sdk["compileSdk"],
        "min_sdk": sdk["minSdk"],
        "target_sdk": sdk["targetSdk"],
    }


JVM_PLUGIN = re.compile(
    r"""kotlin\(\s*["']jvm["']\s*\)|org\.jetbrains\.kotlin\.jvm|["']?java-library["']?|id\s*\(?\s*["']java["']"""
    r"""|alias\(\s*libs\.plugins\.[\w.]*\bjvm\b[\w.]*\s*\)|apply\s+plugin:\s*["'](?:kotlin|java-library|java)["']"""
)


def detect_jvm_modules(root: Path, modules: list[str]) -> list[str]:
    """Modules whose own build file applies a JVM plugin and nothing that could make it Android.

    `apply from:` / convention plugins can add Android indirectly, so those stay Android unless
    Setup records otherwise.
    """
    found = []
    for module in modules:
        directory = module_dir(root, module)
        text = text_if_exists(directory / "build.gradle.kts") or text_if_exists(directory / "build.gradle")
        if JVM_PLUGIN.search(text) and not re.search(r"com\.android|android\s*\{|apply\s+from|\bandroid\b[\w.]*\)", text):
            found.append(module)
    return found


def detect_module_graph(root: Path, modules: list[str]) -> dict[str, list[str]]:
    known = set(modules)
    graph: dict[str, list[str]] = {}
    for module in modules:
        directory = module_dir(root, module)
        text = text_if_exists(directory / "build.gradle.kts") or text_if_exists(directory / "build.gradle")
        dependencies = set(re.findall(r"""project\s*\(\s*['"](:[^'"]+)['"]\s*\)""", text))
        dependencies.update(re.findall(r"""project\s*\(\s*path\s*=\s*['"](:[^'"]+)['"]""", text))
        graph[module] = sorted(dependencies & known)
    return graph


def detect_architecture(root: Path, scan: RepoScan | None = None) -> dict[str, Any]:
    scan = scan or RepoScan(root)
    signals = [name for name in ("domain", "data", "presentation", "ui", "feature", "core") if name in scan.dir_names]
    return {
        "style": "layered" if {"domain", "data"} <= set(signals) else "not_detected",
        "signals": signals,
        "compose": any(path.name.endswith("Screen.kt") for path in scan.kotlin_files)
        or "compose" in scan.gradle_text.lower(),
    }


def detect_conventions(root: Path, modules: list[str], scan: RepoScan | None = None) -> dict[str, Any]:
    scan = scan or RepoScan(root)
    kotlin = scan.kotlin_files
    return {
        "primary_language": "kotlin" if kotlin else ("java" if scan.java_files else "not_detected"),
        "source_roots": [
            str(path.relative_to(root))
            for module in modules
            for path in [module_dir(root, module) / "src/main"]
            if path.exists()
        ],
        "package_samples": [
            match.group(1)
            for path in kotlin[:20]
            if (match := re.search(r"^package\s+([\w.]+)", text_if_exists(path), re.MULTILINE))
        ][:5],
    }


def detect_tests(root: Path, modules: list[str]) -> dict[str, Any]:
    unit_roots, instrumented_roots = [], []
    for module in modules:
        base = module_dir(root, module) / "src"
        if (base / "test").exists():
            unit_roots.append(str((base / "test").relative_to(root)))
        if (base / "androidTest").exists():
            instrumented_roots.append(str((base / "androidTest").relative_to(root)))
    return {"unit_roots": unit_roots, "instrumented_roots": instrumented_roots}


# Formatter plugins, most specific first: (plugin id pattern, check task, format task).
FORMATTERS: tuple[tuple[str, re.Pattern[str], str, str], ...] = (
    ("kotlinter", re.compile(r"org\.jmailen\.kotlinter|jmailen\.gradle"), "lintKotlin", "formatKotlin"),
    ("ktlint-gradle", re.compile(r"org\.jlleitschuh\.gradle\.ktlint"), "ktlintCheck", "ktlintFormat"),
    ("spotless", re.compile(r"com\.diffplug\.spotless"), "spotlessCheck", "spotlessApply"),
)
DETEKT_PLUGIN = re.compile(r"io\.gitlab\.arturbosch\.detekt|dev\.detekt")
BUILD_LOGIC_DIRS = ("build-logic", "buildSrc", "gradle/plugins", "convention", "plugins")


def build_configuration_text(root: Path, scan: RepoScan | None = None) -> str:
    """Gradle scripts, version catalogs and convention-plugin sources, where plugins are applied."""
    if scan is not None:
        logic = [
            path for path in scan.kotlin_files
            if any(path.relative_to(root).as_posix().startswith(name + "/") for name in BUILD_LOGIC_DIRS)
        ]
        return scan.gradle_text + "\n" + "\n".join(text_if_exists(path) for path in logic)
    skip = PRUNE_DIRS
    paths: list[Path] = []
    for current, dirs, files in os.walk(root):
        dirs[:] = [name for name in dirs if name not in skip]
        relative = Path(current).relative_to(root).as_posix()
        in_build_logic = any(relative == name or relative.startswith(name + "/") for name in BUILD_LOGIC_DIRS)
        for name in files:
            if name.endswith((".gradle", ".gradle.kts", ".toml")) or (in_build_logic and name.endswith(".kt")):
                paths.append(Path(current) / name)
    return "\n".join(text_if_exists(path) for path in sorted(paths))


def braced_blocks(text: str, keyword: str) -> list[str]:
    """Bodies of every `keyword { ... }` block, honoring nested braces (flavors nest configs)."""
    blocks = []
    for match in re.finditer(rf"\b{re.escape(keyword)}\s*\{{", text):
        depth, start = 1, match.end()
        for index in range(start, len(text)):
            depth += {"{": 1, "}": -1}.get(text[index], 0)
            if depth == 0:
                blocks.append(text[start:index])
                break
    return blocks


def detect_quality_tools(root: Path, scan: RepoScan | None = None) -> dict[str, Any]:
    text = build_configuration_text(root, scan)
    gradle = "./gradlew" if (root / "gradlew").exists() else "gradle"
    formatter = next(((name, check, fmt) for name, pattern, check, fmt in FORMATTERS if pattern.search(text)), None)
    flavors = sorted({
        name
        for block in braced_blocks(text, "productFlavors")
        for name in re.findall(r"""(?:create|register|maybeCreate)\(\s*["']([A-Za-z0-9_]+)["']""", block)
        + re.findall(r"^\s*([A-Za-z][A-Za-z0-9_]*)\s*\{", block, re.M)
    } - {"all", "configureEach", "getByName", "named"})
    return {
        "formatter": formatter[0] if formatter else None,
        "format_check": f"{gradle} {formatter[1]}" if formatter else None,
        "format_apply": f"{gradle} {formatter[2]}" if formatter else None,
        "detekt": f"{gradle} detekt" if DETEKT_PLUGIN.search(text) else None,
        "has_product_flavors": "productFlavors" in text,
        "product_flavors": flavors,
    }


def detect_tools_and_commands(
    root: Path, config: dict[str, Any], scan: RepoScan | None = None
) -> tuple[dict[str, Any], dict[str, Any]]:
    quality = detect_quality_tools(root, scan)
    commands = dict(config["commands"])
    overridden = config.get("_overridden_commands", set())
    for key in ("format_check", "format_apply", "detekt"):
        if key not in overridden and quality[key]:
            commands[key] = quality[key]
    if "ktlint" not in overridden and commands.get("format_check"):
        commands["ktlint"] = commands["format_check"]
    tools = dict(config["tools"])
    tools["gradle"] = "./gradlew" if (root / "gradlew").exists() else "gradle"
    return tools, commands


OVERRIDES_FILE = Path(".ai") / "android-workflow.json"


def durable_overrides(root: Path) -> dict[str, Any]:
    """Facts the Setup agent (or a human) proved; they survive `clean` and re-detection."""
    path = root / OVERRIDES_FILE
    return read_json(path) if path.is_file() else {}


def git_base_commit(root: Path) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        text=True,
        capture_output=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def build_project_config(root: Path, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    merged_overrides = deep_merge(durable_overrides(root), overrides or {})
    config = deep_merge(DEFAULT_CONFIG, merged_overrides)
    config["_overridden_commands"] = set((merged_overrides.get("commands") or {}).keys())
    modules = detect_modules(root)
    if config.get("jvm_modules") is None:
        config["jvm_modules"] = detect_jvm_modules(root, modules)
    scan = RepoScan(root)
    tools, commands = detect_tools_and_commands(root, config, scan)
    del config["_overridden_commands"]
    return deep_merge(
        config,
        {
            "schema_version": SCHEMA_VERSION,
            "target_root": str(root.resolve()),
            # The gate reads these; detected values fill only what no override set.
            "tools": tools,
            "commands": commands,
            "detected": {
                "versions": detect_versions(root, scan),
                "modules": modules,
                "module_graph": detect_module_graph(root, modules),
                "architecture": detect_architecture(root, scan),
                "conventions": detect_conventions(root, modules, scan),
                "tests": detect_tests(root, modules),
                "tools": tools,
                "commands": commands,
                "quality": detect_quality_tools(root, scan),
            },
        },
    )


def configure(target: Path, override_path: Path | None = None) -> dict[str, Any]:
    overrides = read_json(override_path) if override_path else {}
    if overrides:
        durable = target / OVERRIDES_FILE
        durable.parent.mkdir(parents=True, exist_ok=True)
        write_json(durable, deep_merge(durable_overrides(target), overrides))
    config = build_project_config(target)
    write_json(cache_dir(target) / "project-config.json", config)
    return config


def bootstrap(target: Path, override_path: Path | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    cache = cache_dir(target)
    cache.mkdir(parents=True, exist_ok=True)
    ensure_gitignore(target)
    config_path = cache / "project-config.json"
    env_path = cache / "env.json"
    repo_map_path = cache / "repo-map.json"
    current_commit = git_base_commit(target)
    if not override_path and config_path.exists() and env_path.exists() and repo_map_path.exists() and current_commit:
        cached_map = read_json(repo_map_path)
        if cached_map.get("base_commit") == current_commit:
            return read_json(env_path), cached_map
    # Base commit moved (or no cache): re-detect, so build changes are never read from a stale cache.
    config = configure(target, override_path)
    detected = config["detected"]
    env = {
        "schema_version": SCHEMA_VERSION,
        "target_root": config["target_root"],
        "versions": detected["versions"],
        "tools": detected["tools"],
        "commands": detected["commands"],
        "quality_gates": config["quality_gates"],
        "tests": detected["tests"],
    }
    repo_map = {
        "schema_version": SCHEMA_VERSION,
        "base_commit": current_commit,
        "modules": detected["modules"],
        "module_graph": detected["module_graph"],
        "architecture": detected["architecture"],
        "conventions": detected["conventions"],
    }
    write_json(env_path, env)
    write_json(repo_map_path, repo_map)
    return env, repo_map


SETUP_HELPER = Path.home() / ".ai" / "bin" / "feature_setup.py"
PROFILE_FILE = Path(".ai") / "project-profile.md"
PROFILE_REQUIRED_SECTIONS = ("Build commands", "Lint engine", "Tests", "Code patterns")


def profile_gaps(target: Path) -> list[str]:
    """Profile sections the Setup agent must fill before the workflow trusts them."""
    text = text_if_exists(target / PROFILE_FILE)
    gaps = []
    for heading in PROFILE_REQUIRED_SECTIONS:
        match = re.search(rf"^## {re.escape(heading)}\s*$(.*?)(?=^## |\Z)", text, re.M | re.S)
        rows = [
            line for line in (match.group(1).splitlines() if match else [])
            if line.startswith("|") and not re.match(r"^\|[\s|:-]*\|?$", line)
            and not re.match(r"^\|\s*(What|Module|Area|Rule)\b", line)
            and not re.search(r"\|\s*\|\s*$", line)
        ]
        if not rows:
            gaps.append(f"profile section `{heading}` is empty")
    return gaps


def run_setup(
    target: Path,
    force: bool = False,
    accept: bool = False,
    source_repo: Path | None = None,
) -> dict[str, Any]:
    """Project setup, once per configuration: shared profile + fingerprint, quality tools, overrides.

    Cheap when nothing changed (no probe, no agent). Returns what the Setup agent must resolve.
    """
    if not any((target / name).is_file() for name in ("settings.gradle", "settings.gradle.kts")):
        raise ValueError(f"{target} has no Gradle settings file")
    if source_repo and not (target / OVERRIDES_FILE).is_file() and (source_repo / OVERRIDES_FILE).is_file():
        (target / OVERRIDES_FILE).parent.mkdir(parents=True, exist_ok=True)
        (target / OVERRIDES_FILE).write_text((source_repo / OVERRIDES_FILE).read_text(encoding="utf-8"),
                                             encoding="utf-8")
    helper: dict[str, Any] = {"status": "unavailable", "requires_explorer": True}
    if SETUP_HELPER.is_file():
        flags = ["--accept"] if accept else ["--ensure"] + (["--force"] if force else [])
        if source_repo and not accept:
            flags += ["--source-repo", str(source_repo.resolve())]
        result = subprocess.run(
            [sys.executable, str(SETUP_HELPER), "--repo-root", str(target), *flags],
            text=True, capture_output=True, check=False,
        )
        try:
            helper = json.loads(result.stdout.strip().splitlines()[-1])
        except (IndexError, json.JSONDecodeError):
            helper = {"status": "error", "error": (result.stderr or result.stdout).strip()[-400:]}
        if helper.get("status") == "error":
            raise ValueError(f"setup helper failed: {helper.get('error')}")
    ensure_gitignore(target)
    config = configure(target)
    quality = config["detected"]["quality"]
    overrides = durable_overrides(target)
    reasons: list[str] = []
    if helper.get("requires_explorer"):
        reasons.append("profile is missing, stale or not accepted")
    reasons.extend(profile_gaps(target))
    if quality["has_product_flavors"] and "unit_tests" not in (overrides.get("module_commands") or {}):
        reasons.append(
            "product flavors found "
            f"({', '.join(quality['product_flavors']) or 'names unresolved'}); "
            "record the real compile/test/lint/install tasks in .ai/android-workflow.json"
        )
    if not config["commands"].get("format_check") and "format_check" not in (overrides.get("commands") or {}):
        reasons.append("no formatter detected; confirm none exists or record it")
    if not (config.get("device") or {}).get("application_id"):
        reasons.append("debug applicationId / launch entry not recorded")
    if accept and reasons:
        raise ValueError("cannot accept setup: " + "; ".join(reasons))
    return {
        "status": "ready" if not reasons else "needs_setup_agent",
        "reasons": reasons,
        "profile": str(target / PROFILE_FILE),
        "probe": helper.get("probe"),
        "overrides_file": str(target / OVERRIDES_FILE),
        "quality": quality,
        "gate_commands": {key: config["commands"].get(key) for key in (
            "format_check", "format_apply", "detekt", "build", "unit_tests", "android_lint", "install")},
        "module_commands": config["module_commands"],
        "jvm_modules": config.get("jvm_modules") or [],
        "jvm_module_commands": config.get("jvm_module_commands"),
    }


def normalize_question(value: Any, index: int) -> dict[str, Any]:
    if isinstance(value, dict):
        return {
            "id": str(value.get("id", f"business-{index}")),
            "question": str(value.get("question", "")),
            "answer": value.get("answer"),
        }
    return {"id": f"business-{index}", "question": str(value), "answer": None}


def route_for(ticket_type: str, complexity: str, risk: str, surfaces: list[str]) -> list[str]:
    route = ["T0", "T1", "T2"]
    if ticket_type == "bug" or complexity != "low" or risk != "low":
        route.append("T3")
    route.extend(["T4", "T5", "T6"])
    device_surfaces = {
        "ui", "navigation", "permissions", "lifecycle", "runtime_di", "room_migration",
        "notifications", "deeplink", "workmanager",
    }
    if device_surfaces.intersection(surfaces):
        route.append("T7")
    route.extend(["T8", "T9"])
    return route


def infer_surfaces(ticket: dict[str, Any]) -> list[str]:
    explicit = [str(item).strip() for item in ticket.get("surfaces") or [] if str(item).strip()]
    if explicit:
        return explicit
    tokens = set(
        re.findall(
            r"[a-zà-ÿ]+",
            f"{ticket.get('title') or ''} {ticket.get('description') or ''}".lower(),
        )
    )
    return [surface for surface, words in SURFACE_KEYWORDS if words & tokens]


# Ticket text may be English or Portuguese, so each surface lists both spellings.
SURFACE_KEYWORDS: tuple[tuple[str, frozenset[str]], ...] = (
    ("notifications", frozenset({
        "notification", "notifications", "reminder", "reminders", "alarm", "push",
        "notificação", "notificações", "notificacao", "lembrete",
    })),
    ("ui", frozenset({
        "icon", "ui", "compose", "layout", "screen", "button", "dialog", "sheet", "toolbar",
        "card", "banner", "color", "colour", "theme", "text", "label", "image", "animation",
        "tela", "botão", "botao", "ícone", "icone", "cor", "tema", "texto", "imagem", "animação",
    })),
    ("navigation", frozenset({
        "navigation", "navigate", "tab", "tabs", "back", "navegação", "navegacao", "aba",
    })),
    ("deeplink", frozenset({"deeplink", "deeplinks", "applink", "universal"})),
    ("permissions", frozenset({"permission", "permissions", "permissão", "permissao"})),
    ("lifecycle", frozenset({"lifecycle", "rotation", "foreground", "background", "resume"})),
    ("workmanager", frozenset({"workmanager", "worker", "periodic", "sync"})),
    ("room_migration", frozenset({"room", "migration", "database", "migração", "migracao"})),
)


def find_pr_template(root: Path) -> str | None:
    files = (
        root / ".github" / "PULL_REQUEST_TEMPLATE.md",
        root / ".github" / "pull_request_template.md",
        root / "docs" / "pull_request_template.md",
        root / "PULL_REQUEST_TEMPLATE.md",
        root / ".azuredevops" / "pull_request_template.md",
        root / ".bitbucket" / "PULL_REQUEST_TEMPLATE.md",
    )
    for path in files:
        if path.is_file():
            return path.relative_to(root).as_posix()
    for folder in (
        root / ".github" / "PULL_REQUEST_TEMPLATE",
        root / ".gitlab" / "merge_request_templates",
    ):
        if not folder.is_dir():
            continue
        matches = sorted(path for path in folder.glob("*.md") if path.is_file())
        if matches:
            return matches[0].relative_to(root).as_posix()
    return None


def ticket_spec(ticket: dict[str, Any], target: Path | None = None) -> dict[str, Any]:
    ticket_type = str(ticket.get("type", "chore"))
    complexity = str(ticket.get("complexity", "low"))
    risk = str(ticket.get("risk", "low"))
    surfaces = infer_surfaces(ticket)
    questions = [normalize_question(value, index) for index, value in enumerate(ticket.get("business_questions", []), 1)]
    return {
        "schema_version": SCHEMA_VERSION,
        "ticket": {"id": ticket.get("id"), "title": ticket.get("title"), "description": ticket.get("description", "")},
        "type": ticket_type,
        "reproduction": ticket.get("reproduction"),
        "acceptance_criteria": [str(item) for item in ticket.get("acceptance_criteria", [])],
        "surfaces": surfaces,
        "complexity": complexity,
        "risk": risk,
        "business_questions": questions,
        "route": route_for(ticket_type, complexity, risk, surfaces),
        "pr_template": find_pr_template(target) if target else None,
    }


RUN_VERDICTS = ("gate-report.json", "review.json", "device-report.md", "delivery.json", "prebuild.json")


def initialize_later_artifacts(agent_dir: Path) -> None:
    artifacts = {
        "change-set-map.json": {
            "schema_version": 1, "candidate_files": [], "symbols": [], "related_tests": [],
            "extension_points": [], "neighbor_patterns": [], "affected_modules": [],
            "public_api_changed": False,
        },
        "gate-report.json": {
            "schema_version": 1, "status": "not_run", "steps": [], "retries": 0,
            "prohibitions_respected": True,
        },
        "review.json": {
            "schema_version": 1, "status": "not_run", "blocking": [], "concerns": [], "suggestions": [],
        },
        "stage-metrics.json": {"schema_version": 1, "stages": {}},
    }
    for name, value in artifacts.items():
        path = agent_dir / name
        if not path.exists():
            write_json(path, value)
    markdown = {
        "plan.md": "# Objective\n\nNot required for this route.\n\n# Steps\n\n# Out of scope\n\n# Verification\n",
        "implementation-notes.md": "# Decisions\n\nHost has not implemented yet.\n\n# Trade-offs\n\n# Out of scope\n\n# Assumptions\n",
        "device-report.md": "# Status\n\nnot_run\n\n# Device\n\n# Scenarios\n\n# Evidence\n\n# Not verified\n",
        "stage-log.md": "# Stage log\n\n| time | stage | status | actor | note |\n|---|---|---|---|---|\n",
    }
    for name, value in markdown.items():
        path = agent_dir / name
        if not path.exists():
            path.write_text(value, encoding="utf-8")


def pending_question(spec: dict[str, Any], answers: dict[str, str]) -> dict[str, Any] | None:
    for question in spec["business_questions"]:
        if question.get("answer") is None and question["id"] not in answers:
            return {"id": question["id"], "question": question["question"]}
    return None


def stage_start(state: dict[str, Any], stage: str) -> float:
    state["current_stage"] = stage
    state["stages"][stage] = {"status": "running"}
    return time.monotonic()


def stage_end(
    state: dict[str, Any],
    metrics: dict[str, Any],
    stage: str,
    started: float,
    status: str = "completed",
    **details: Any,
) -> None:
    duration = round(time.monotonic() - started, 6)
    state["stages"][stage] = {"status": status, **details}
    metrics["stages"][stage] = {
        "status": status,
        "wall_time_seconds": duration,
        "attempts": details.get("attempts", 0 if status == "awaiting_host" else 1),
        "tokens": details.get("tokens"),
    }


def record_stage_metrics(
    metrics: dict[str, Any],
    stage: str,
    status: str,
    seconds: float | None = None,
    tokens: int | None = None,
    attempts: int = 1,
) -> None:
    """Accumulate across rounds: a stage that runs twice costs twice. Unknown stays None, never 0."""
    entry = metrics.setdefault("stages", {}).setdefault(stage, {})
    entry["status"] = status
    if seconds is not None:
        entry["wall_time_seconds"] = round((entry.get("wall_time_seconds") or 0) + max(seconds, 0), 3)
    else:
        entry.setdefault("wall_time_seconds", None)
    if tokens is not None:
        entry["tokens"] = (entry.get("tokens") or 0) + tokens
    else:
        entry.setdefault("tokens", None)
    entry["attempts"] = (entry.get("attempts") or 0) + attempts


def record_totals(metrics: dict[str, Any], state: dict[str, Any]) -> None:
    reported = [
        item["tokens"] for item in metrics.get("stages", {}).values()
        if isinstance(item, dict) and isinstance(item.get("tokens"), int)
    ]
    created = state.get("created_at")
    metrics["totals"] = {
        "wall_time_seconds": max(int(time.time()) - int(created), 0) if created else None,
        "tokens": sum(reported) if reported else None,
        "stages_reporting_tokens": len(reported),
    }


def locate_change_set(target: Path, spec: dict[str, Any], repo_map: dict[str, Any]) -> dict[str, Any]:
    raw_terms = re.findall(
        r"[A-Za-zÀ-ÿ_][A-Za-zÀ-ÿ0-9_]{2,}",
        " ".join(
            [
                str(spec["ticket"].get("title") or ""),
                str(spec["ticket"].get("description") or ""),
                *spec["acceptance_criteria"],
            ]
        ),
    )
    ignored = {
        "para", "com", "uma", "que", "the", "and", "android", "quando", "deve", "from",
        "correct", "small", "smarter", "plus", "with", "this", "that", "into", "your",
        "have", "for", "not", "are", "was", "been", "will", "can", "should", "when",
        "user", "users", "show", "add", "fix", "update", "new", "make", "after", "before",
        "must", "only", "also", "all", "any", "some", "screen", "app", "feature", "bug",
        # Portuguese function words, for tickets written in Portuguese.
        "por", "pelo", "pela", "como", "mais", "sem", "dos", "das", "nos", "nas", "sobre",
        "após", "antes", "usuário", "usuario", "mostrar", "exibir", "adicionar", "corrigir",
        "alterar", "ajustar", "novo", "nova", "tela", "não", "nao", "ser", "está", "esta",
    }
    skip_parts = PRUNE_DIRS
    terms = {term.lower() for term in raw_terms if term.lower() not in ignored}
    scored: list[tuple[int, dict[str, Any]]] = []
    source_paths: list[Path] = []
    suffixes = {".kt", ".java", ".xml"}
    for root, dirs, files in os.walk(target):
        dirs[:] = [name for name in dirs if name not in skip_parts]
        source_paths.extend(Path(root) / name for name in files if Path(name).suffix in suffixes)
    for path in source_paths:
        relative = str(path.relative_to(target))
        haystack_path = f"{path.stem} {relative}".lower()
        content = None
        matched: list[str] = []
        score = 0
        for term in terms:
            if term in haystack_path:
                score += 3
                matched.append(term)
                continue
            if content is None:
                try:
                    size = path.stat().st_size
                except OSError:
                    size = 0
                content = text_if_exists(path).lower() if size and size <= 64_000 else ""
            if term in content:
                score += 1
                matched.append(term)
        if matched:
            scored.append((score, {"path": relative, "matched_terms": sorted(matched)[:8]}))
    scored.sort(key=lambda item: (-item[0], item[1]["path"]))
    candidates = [item[1] for item in scored[:12]]
    modules = repo_map["modules"]
    affected = sorted(
        {
            module
            for item in candidates
            for module in modules
            if item["path"].startswith(str(module_dir(target, module).relative_to(target)) + "/")
        }
    )
    candidate_stems = {Path(item["path"]).stem.removesuffix("Test") for item in candidates}
    related_tests = [
        str(path.relative_to(target))
        for path in source_paths
        if "test" in {part.lower() for part in path.parts}
        and any(stem and stem in path.stem for stem in candidate_stems)
    ][:12]
    return {
        "schema_version": 1,
        "candidate_files": candidates,
        "symbols": [],
        "related_tests": related_tests[:12],
        "extension_points": [],
        "neighbor_patterns": [],
        "affected_modules": affected,
        "public_api_changed": False,
    }


def write_plan(agent_dir: Path, spec: dict[str, Any], change_set: dict[str, Any]) -> None:
    candidates = "\n".join(f"- `{item['path']}`" for item in change_set["candidate_files"][:10]) or "- Target not located yet"
    bug_step = "- Reproduce deterministically and add a failing test before the fix.\n" if spec["type"] == "bug" else ""
    content = (
        f"# Objective\n\n{spec['ticket'].get('title') or 'Execute the given ticket.'}\n\n"
        f"# Steps\n\n{bug_step}- Confirm candidate files.\n- Implement the smallest change.\n"
        f"- Validate acceptance criteria and tests.\n\n# Candidate files\n\n{candidates}\n\n"
        "# Out of scope\n\n- Files and modules outside `change-set-map.json`.\n\n"
        "# Verification\n\n- Incremental gates for affected modules.\n"
    )
    (agent_dir / "plan.md").write_text(content, encoding="utf-8")


def execute_command(
    command: str,
    target: Path,
    timeout: int,
    full_output: bool = False,
) -> dict[str, Any]:
    """Run `command` in `target` and keep the tail of its output."""
    started = time.monotonic()
    try:
        result = subprocess.run(
            shlex.split(command),
            cwd=target,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        combined = (result.stdout + "\n" + result.stderr).strip().splitlines()
        command_result = {
            "command": command,
            "exit_code": result.returncode,
            "duration_seconds": round(time.monotonic() - started, 3),
            "output_excerpt": combined[-25:],
        }
        if full_output:
            command_result["output"] = combined
        return command_result
    except (OSError, subprocess.TimeoutExpired) as error:
        return {
            "command": command,
            "exit_code": 124 if isinstance(error, subprocess.TimeoutExpired) else 127,
            "duration_seconds": round(time.monotonic() - started, 3),
            "output_excerpt": [str(error)],
            **({"output": [str(error)]} if full_output else {}),
        }


def secret_scan(target: Path) -> dict[str, Any]:
    repo_map_path = cache_dir(target) / "repo-map.json"
    base = read_json(repo_map_path).get("base_commit") if repo_map_path.exists() else None
    result = subprocess.run(
        ["git", "-C", str(target), "diff", base or "HEAD", "--no-ext-diff", "--unified=0"],
        text=True,
        capture_output=True,
        check=False,
    )
    status = subprocess.run(
        ["git", "-C", str(target), "status", "--porcelain"],
        text=True,
        capture_output=True,
        check=False,
    )
    lines = result.stdout.splitlines()
    for entry in status.stdout.splitlines():
        if not entry.startswith("?? "):
            continue
        path = target / entry[3:]
        if path.is_file() and path.stat().st_size <= 1_000_000:
            lines.extend(f"+{line}" for line in text_if_exists(path).splitlines())
    patterns = [
        re.compile(r"BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY"),
        re.compile(r"(?i)(?:api[_-]?key|secret|token|password)\s*[:=]\s*['\"][^'\"]{8,}"),
    ]
    findings = []
    for index, line in enumerate(lines, 1):
        if line.startswith("+") and not line.startswith("+++") and any(pattern.search(line) for pattern in patterns):
            findings.append({"diff_line": index, "kind": "possible_secret"})
    return {"status": "failed" if findings else "passed", "findings": findings}


def modules_for_paths(target: Path, paths: list[str], modules: list[str]) -> list[str]:
    """Map changed files to the deepest Gradle module that contains them."""
    found: set[str] = set()
    for name in paths:
        best: tuple[int, str] | None = None
        for module in modules:
            prefix = module_dir(target, module).relative_to(target).as_posix() + "/"
            if name.startswith(prefix) and (best is None or len(prefix) > best[0]):
                best = (len(prefix), module)
        if best:
            found.add(best[1])
    return sorted(found)


def gate_modules(target: Path, change_set: dict[str, Any]) -> list[str]:
    """Gate the modules the Implementer actually touched, not the Localizer's guess."""
    return gate_scope(target, change_set)[0]


def gate_scope(target: Path, change_set: dict[str, Any]) -> tuple[list[str], bool]:
    """Modules to gate, and whether every changed file lives inside one of them."""
    repo_map_path = cache_dir(target) / "repo-map.json"
    cached = read_json(repo_map_path).get("modules", []) if repo_map_path.exists() else []
    modules = detect_modules(target) or cached
    changes = source_changes(target)
    touched = modules_for_paths(target, changes, modules)
    if not touched:
        return list(change_set.get("affected_modules") or []), False
    covered = all(modules_for_paths(target, [name], modules) for name in changes)
    return touched, covered


DECLARATION_PATTERN = re.compile(
    r"^(?!(?:[a-z]+[ \t]+)*(?:private|internal)[ \t])(?:[a-z]+[ \t]+)*(?:class|interface|object|typealias)"
    r"[ \t]+([A-Z]\w*)",
    re.M,
)
SOURCE_SUFFIXES = frozenset({".kt", ".java"})


def changed_declarations(target: Path, changes: list[str]) -> set[str]:
    """Type names another module could reference: each changed main-source file's name and its
    top-level declarations that are neither private nor internal (nested types go through them)."""
    names: set[str] = set()
    for name in changes:
        path = Path(name)
        if path.suffix not in SOURCE_SUFFIXES or "main" not in path.parts:
            continue
        names.add(path.stem)
        names.update(DECLARATION_PATTERN.findall(text_if_exists(target / name)))
    return names


def consumer_modules(
    target: Path, config: dict[str, Any], touched: list[str], changes: list[str],
) -> tuple[list[str], list[str]]:
    """Modules that depend on a touched module and reference one of its changed declarations.

    The gate otherwise builds only the touched modules, so a changed type breaks its callers in CI
    first. Returns the modules to gate and the ones left out over `max_consumer_modules`.
    """
    graph = (config.get("detected") or {}).get("module_graph") or {}
    names = changed_declarations(target, changes)
    if not graph or not names or not touched:
        return [], []
    dependents: dict[str, set[str]] = {}
    for module, dependencies in graph.items():
        for dependency in dependencies:
            dependents.setdefault(dependency, set()).add(module)
    seen = set(touched)
    queue = list(touched)
    candidates: list[str] = []
    while queue:
        for module in sorted(dependents.get(queue.pop(0), ())):
            if module not in seen:
                seen.add(module)
                candidates.append(module)
                queue.append(module)
    if not candidates:
        return [], []
    directories = [module_dir(target, module).relative_to(target).as_posix() for module in candidates]
    patterns = [argument for name in sorted(names) for argument in ("-e", name)]
    hits = git_lines(target, "grep", "-l", "-w", "-F", *patterns, "--", *directories) or []
    found = set(modules_for_paths(target, hits, candidates))
    consumers = [module for module in candidates if module in found]
    limit = int((config.get("quality_gates") or {}).get("max_consumer_modules", 12))
    return consumers[:limit], consumers[limit:]


COVERS_PATTERN = re.compile(r"^covers:[ \t]*\n((?:[ \t]+-[ \t]*\S.*\n?)+)", re.M)


def doc_covers(text: str) -> list[str]:
    """The `covers:` list of a doc's YAML frontmatter; empty when it has none."""
    if not text.startswith("---"):
        return []
    frontmatter = text.split("\n---", 1)[0]
    found = COVERS_PATTERN.search(frontmatter + "\n")
    if not found:
        return []
    entries = (line.strip()[1:].strip().strip("'\"") for line in found.group(1).splitlines())
    return [entry.rstrip("/") for entry in entries if entry]


def feature_docs_step(target: Path, config: dict[str, Any], changes: list[str]) -> dict[str, Any] | None:
    """Fail when a production file changed under a path a feature doc `covers:` and that doc did not.

    Reads the working tree, so it holds before anything is committed. Off unless the project
    records `feature_docs.glob`.
    """
    pattern = (config.get("feature_docs") or {}).get("glob")
    if not pattern:
        return None
    # `changes` holds source files only; the docs themselves (.md) come from the full change list.
    edited = set(changes) | set(changed_paths(target)[0])
    stale: list[str] = []
    for doc in sorted(target.glob(pattern)):
        relative = doc.relative_to(target).as_posix()
        if relative in edited:
            continue
        covered = [
            name for name in changes
            for cover in doc_covers(text_if_exists(doc))
            if name.startswith(cover + "/") and "main" in Path(name).parts
        ]
        if covered:
            stale.append(f"{relative} covers {len(covered)} changed file(s) but was not updated: " + ", ".join(covered[:5]))
    return {
        "command": f"feature docs ({pattern})",
        "exit_code": 1 if stale else 0,
        "outcome": "failed" if stale else "passed",
        "duration_seconds": 0.0,
        "output_excerpt": stale,
    }


GRADLE_EXECUTABLES = frozenset({"gradlew", "gradle", "gradlew.bat"})


def gradle_invocation(command: str) -> tuple[str, list[str]] | None:
    """`(program, tasks)` for a plain Gradle task command; None when it has flags or is not Gradle."""
    try:
        parts = shlex.split(command)
    except ValueError:
        return None
    if len(parts) < 2 or Path(parts[0]).name not in GRADLE_EXECUTABLES:
        return None
    if any(part.startswith("-") for part in parts[1:]):
        return None
    return parts[0], parts[1:]


def scoped_format_checks(format_check: str, modules: list[str]) -> list[str]:
    """`./gradlew lintKotlin` → `./gradlew :a:lintKotlin ...`; anything else is kept whole."""
    invocation = gradle_invocation(format_check)
    if not invocation or not modules:
        return [format_check]
    program, tasks = invocation
    if len(tasks) != 1 or ":" in tasks[0]:
        return [format_check]
    return [shlex.join([program, f"{module}:{tasks[0]}"]) for module in modules]


def gate_commands(
    config: dict[str, Any],
    change_set: dict[str, Any],
    needs_device: bool,
    modules: list[str] | None = None,
    scope_format: bool = False,
    consumers: list[str] | None = None,
) -> list[str]:
    """Order steps so the cheapest likely failure runs first; lint is last because it is slowest.

    The APK is not assembled here: the Device stage builds and installs it once. With
    `scope_format`, the formatter runs only in `modules` (every changed file must live in one).
    """
    del needs_device  # kept for call-site compatibility
    commands: list[str] = []
    format_check = config["commands"].get("format_check") or config["commands"].get("ktlint")
    if format_check:
        commands.extend(scoped_format_checks(format_check, modules or []) if scope_format else [format_check])
    modules = change_set["affected_modules"] if modules is None else modules
    if modules:
        jvm = set(config.get("jvm_modules") or [])
        known = set((config.get("detected") or {}).get("modules") or [])
        unknown = [module for module in modules if module not in known]
        if unknown and config.get("target_root"):
            jvm.update(detect_jvm_modules(Path(config["target_root"]), unknown))
        unknown = [module for module in consumers or [] if module not in known and module not in jvm]
        if unknown and config.get("target_root"):
            jvm.update(detect_jvm_modules(Path(config["target_root"]), unknown))
        for name in ("compile", "unit_tests", "android_lint"):
            for module in [*modules, *(consumers if name != "android_lint" and consumers else [])]:
                table = config.get("jvm_module_commands", {}) if module in jvm else config["module_commands"]
                template = table.get(name)
                if template:
                    commands.append(template.format(module=module))
        if config["commands"].get("detekt"):
            commands.append(config["commands"]["detekt"])
    else:
        commands.extend(
            command
            for command in (
                config["commands"].get("build"),
                config["commands"].get("unit_tests"),
                config["commands"].get("detekt"),
                config["commands"].get("android_lint"),
            )
            if command
        )
    if change_set.get("public_api_changed") and config["commands"].get("unit_tests"):
        commands.append(config["commands"]["unit_tests"])
    return list(dict.fromkeys(commands))


PASSING_OUTCOMES = frozenset({"passed", "waived"})
EXPRESS_MAX_FILES = 3
FAILED_TASK_PATTERN = re.compile(r"Execution failed for task '([^']+)'")
TASK_LINE_PATTERN = re.compile(r"^> Task (:\S*)(?:\s+(\S.*))?$")
MISSING_TASK_PATTERN = re.compile(r"Cannot locate tasks? that match '([^']+)'")
BASELINE_CREATED_PATTERN = re.compile(r"Created baseline file (\S+)")
BASELINE_ABORT = "Aborting build since new baseline file was created"
BASELINE_NAME_PATTERN = re.compile(r"(?i)baseline[^/]*\.xml$")
LINT_REPORT_PATTERN = re.compile(r"The full lint text report is located at:\s*(\S+)")
LINT_ISSUE_PATTERN = re.compile(r"^(.+?):(?:\d+:)? (Error|Fatal|Warning): (.+) \[([\w-]+)(?: from [^\]]+)?\]$")
KOTLINTER_ISSUE_PATTERN = re.compile(r"^(\S+?\.kts?):\d+:\d+:? Lint error > (.+)$")
LINT_SUMMARY_PATTERN = re.compile(r"^\s*(\d+) errors?, (\d+) warnings?", re.M)
WAIVABLE_CATEGORIES = frozenset({"android_lint", "format"})
# Formatter, compiler and test findings are printed before the failure summary, not inside it.
DIAGNOSTIC_LINE_PATTERN = re.compile(r"Lint error >|^e: |: error: | FAILED$|\.(?:kt|kts|java|xml):\d+")


def step_passes(step: dict[str, Any]) -> bool:
    outcome = step.get("outcome") or ("passed" if step.get("exit_code") == 0 else "failed")
    return outcome in PASSING_OUTCOMES


def gate_steps_pass(steps: list[dict[str, Any]]) -> bool:
    return all(step_passes(step) for step in steps)


def gate_units(commands: list[str], batch: bool) -> list[list[str]]:
    """Plain Gradle task commands share one invocation; anything else runs on its own, in order."""
    units: list[list[str]] = []
    batch_index: int | None = None
    program: str | None = None
    for command in commands:
        invocation = gradle_invocation(command)
        if batch and invocation and program in (None, invocation[0]):
            if batch_index is None:
                batch_index, program = len(units), invocation[0]
                units.append([])
            units[batch_index].append(command)
        else:
            units.append([command])
    return units


def failure_blocks(lines: list[str]) -> dict[str, list[str]]:
    """`* What went wrong` text per failed task path."""
    blocks: dict[str, list[str]] = {}
    current: str | None = None
    for line in lines:
        match = FAILED_TASK_PATTERN.search(line)
        if match:
            current = match.group(1)
            blocks.setdefault(current, []).append(line.strip())
            continue
        stripped = line.strip()
        if current is None:
            continue
        if stripped.startswith(("* Try:", "* What went wrong", "BUILD FAILED")) or re.match(r"^={5,}", stripped):
            current = None
            continue
        blocks[current].append(line.rstrip())
    return blocks


def task_matches(requested: str, executed: str, prefix: bool) -> bool:
    """`:a:lintKotlin` covers `:a:lintKotlinMain`; a bare name selects that task in every project."""
    project, _, name = executed.rpartition(":")
    if requested.startswith(":"):
        want_project, _, want_name = requested.rpartition(":")
        if project != want_project:
            return False
    else:
        want_name = requested
    return name.startswith(want_name) if prefix else name == want_name


def failure_category(command: str) -> str | None:
    """Classify lint and formatter tasks for their diagnostic output."""
    invocation = gradle_invocation(command)
    if not invocation:
        return None
    names = [task.rpartition(":")[2].lower() for task in invocation[1]]
    if any(name.startswith("lintkotlin") or "ktlint" in name or "spotless" in name for name in names):
        return "format"
    if any(name.startswith("lint") for name in names):
        return "android_lint"
    return None


def relative_to_root(path: str, root: Path) -> str:
    text = path[len("file://"):] if path.startswith("file://") else path
    candidate = Path(text)
    if candidate.is_absolute():
        try:
            return candidate.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            return candidate.as_posix()
    return candidate.as_posix()


def step_findings(command: str, block: list[str], lines: list[str], root: Path) -> list[tuple[str, str]]:
    """`(file, finding)` pairs, keyed without line numbers so an edit above an old finding is not new."""
    category = failure_category(command)
    found: list[tuple[str, str]] = []
    if category == "android_lint":
        reports = dict.fromkeys(LINT_REPORT_PATTERN.findall("\n".join(block)))
        for report in reports:
            for line in text_if_exists(Path(report)).splitlines():
                match = LINT_ISSUE_PATTERN.match(line.strip())
                if match:
                    path = relative_to_root(match.group(1), root)
                    found.append((path, f"{path} [{match.group(4)}] {match.group(3)}"))
        # Some AGP versions print the finding inline but omit the report path from an individual
        # task's failure block when multiple lint tasks run together. Preserve changed-file
        # classification in that case by reading the same diagnostic format directly.
        if not reports:
            for line in block:
                match = LINT_ISSUE_PATTERN.match(line.strip())
                if match:
                    path = relative_to_root(match.group(1), root)
                    found.append((path, f"{path} [{match.group(4)}] {match.group(3)}"))
    elif category == "format":
        tasks = (gradle_invocation(command) or ("", []))[1]
        prefixes = [
            module_dir(root, task.rpartition(":")[0]).relative_to(root).as_posix() + "/"
            for task in tasks
            if task.startswith(":") and task.rpartition(":")[0]
        ]
        for line in lines:
            match = KOTLINTER_ISSUE_PATTERN.match(line.strip())
            if not match:
                continue
            path = relative_to_root(match.group(1), root)
            if not prefixes or any(path.startswith(prefix) for prefix in prefixes):
                found.append((path, f"{path} {match.group(2)}"))
    return found


def step_diagnostics(command: str, block: list[str], lines: list[str], root: Path) -> list[str]:
    return [key for _, key in step_findings(command, block, lines, root)]


def lint_report_total(block: list[str]) -> int | None:
    """Findings the lint text reports announce (`3 errors, 2 warnings`); None when none say."""
    totals = [
        int(match.group(1)) + int(match.group(2))
        for report in dict.fromkeys(LINT_REPORT_PATTERN.findall("\n".join(block)))
        for match in [LINT_SUMMARY_PATTERN.search(text_if_exists(Path(report)))]
        if match
    ]
    return sum(totals) if totals else None


def related_diagnostics(tasks: list[str], lines: list[str], root: Path) -> list[str]:
    """Up to 15 finding lines from the modules these tasks belong to (test failures name no path)."""
    folders = []
    for task in tasks:
        project = task.rpartition(":")[0] if task.startswith(":") else ""
        if project:
            folder = module_dir(root, project)
            folders += [f"{folder}/", f"{folder.relative_to(root).as_posix()}/"]
    related = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("> Task") or not DIAGNOSTIC_LINE_PATTERN.search(stripped):
            continue
        if not folders or stripped.endswith(" FAILED") or any(folder in stripped for folder in folders):
            related.append(stripped)
    return related[:15]


def run_gradle_batch(
    program: str,
    commands: list[str],
    root: Path,
    timeout: int,
    log_path: Path,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Run every task in one `--continue` build and give each original command its own outcome.

    `timeout` is per command, as when each ran alone, so the batch keeps the same total bound.
    """
    tasks = list(dict.fromkeys(task for command in commands for task in (gradle_invocation(command) or ("", []))[1]))
    batch_args = [program, *tasks]
    lint_init_script: Path | None = None
    if any(failure_category(command) == "android_lint" for command in commands):
        batch_args.extend([
            "-Pandroid.experimental.lint.missingBaselineIsEmptyBaseline=true",
            "-Dlint.baselines.continue=true",
        ])
        root_init_script = log_path.with_name(f"{log_path.stem}-lint.init.gradle")
        root_init_script.parent.mkdir(parents=True, exist_ok=True)
        root_init_script.write_text(
            """gradle.afterProject { project, state ->
    def android = project.extensions.findByName('android')
    if (android != null) {
        android.lint.warningsAsErrors = true
    }
}
""",
            encoding="utf-8",
        )
        lint_init_script = root_init_script
        batch_args.extend(["--init-script", str(lint_init_script)])
    batch = shlex.join([*batch_args, "--continue", "--console=plain"])
    try:
        result = execute_command(batch, root, timeout * len(commands), full_output=True)
    finally:
        if lint_init_script is not None:
            lint_init_script.unlink(missing_ok=True)
    lines = result.pop("output")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    code = result["exit_code"]
    task_lines = [match for match in map(TASK_LINE_PATTERN.match, lines) if match]
    seen = [match.group(1) for match in task_lines]
    blocks = failure_blocks(lines)
    failed = list(dict.fromkeys([
        *blocks,
        *(match.group(1) for match in task_lines if (match.group(2) or "").startswith("FAILED")),
    ]))
    steps: list[dict[str, Any]] = []
    for index, command in enumerate(commands):
        own_tasks = (gradle_invocation(command) or ("", []))[1]
        own_failed = [task for task in failed if any(task_matches(want, task, True) for want in own_tasks)]
        ran = all(any(task_matches(want, task, False) for task in seen) for want in own_tasks)
        if code == 0:
            outcome = "passed"
        elif own_failed:
            outcome = "failed"
        else:
            outcome = "passed" if ran else "not_run"
        step: dict[str, Any] = {
            "command": command,
            "exit_code": 0 if outcome == "passed" else (code or 1),
            "duration_seconds": result["duration_seconds"] if index == 0 else None,
            "outcome": outcome,
            "log": str(log_path),
            "output_excerpt": [],
        }
        if index == 0:
            step["batch"] = batch
        if outcome == "failed":
            block = [line for task in own_failed for line in blocks.get(task, [])]
            step["failed_tasks"] = own_failed
            step["output_excerpt"] = (
                [line for line in block if line.strip()][:30] + related_diagnostics(own_tasks, lines, root)
            ) or lines[-25:]
            findings = step_findings(command, block, lines, root)
            step["_findings"] = findings
            step["_diagnostics"] = [key for _, key in findings]
            if failure_category(command) == "android_lint":
                step["_expected_findings"] = lint_report_total(block)
        elif outcome == "not_run":
            step["output_excerpt"] = (
                ["not run: a task it depends on failed; see the failed steps"] if failed else lines[-25:]
            )
        steps.append(step)
    return steps, lines


def git_lines(target: Path, *args: str) -> list[str] | None:
    result = subprocess.run(["git", "-C", str(target), *args], text=True, capture_output=True, check=False)
    return [line for line in result.stdout.splitlines() if line] if result.returncode == 0 else None


def git_tracked(target: Path, relative: str) -> bool:
    return git_lines(target, "ls-files", "--error-unmatch", "--", relative) is not None


def untracked_baselines(target: Path) -> set[str]:
    listed = git_lines(target, "ls-files", "--others", "--exclude-standard") or []
    return {name for name in listed if BASELINE_NAME_PATTERN.search(name)}


def settle_lint_baselines(
    target: Path,
    steps: list[dict[str, Any]],
    lines: list[str],
    before: set[str],
) -> list[str]:
    """Clean up a baseline created by lint when the AGP empty-baseline option is unsupported.

    Such a run has no lint verdict, so delete only newly created, untracked files and mark it
    unverified. Supported AGP versions analyze a missing baseline as empty and return findings.
    """
    created = untracked_baselines(target) - before
    for match in BASELINE_CREATED_PATTERN.finditer("\n".join(lines)):
        relative = relative_to_root(match.group(1), target)
        if relative not in before and not Path(relative).is_absolute() and (target / relative).is_file():
            created.add(relative)
    removed = []
    for relative in sorted(created):
        if (target / relative).is_file() and not git_tracked(target, relative):
            (target / relative).unlink()
            removed.append(relative)
    # Older AGP may create a baseline despite the empty-baseline option; it still hid all findings.
    baseline_dirs = {Path(relative).parent.as_posix() for relative in removed}
    warnings = []
    for step in steps:
        aborted = step["outcome"] == "failed" and any(BASELINE_ABORT in line for line in step["output_excerpt"])
        absorbed = step["outcome"] == "passed" and failure_category(step["command"]) == "android_lint" and any(
            module_dir(target, task.rpartition(":")[0]).relative_to(target).as_posix() in baseline_dirs
            for task in (gradle_invocation(step["command"]) or ("", []))[1]
            if task.startswith(":") and task.rpartition(":")[0]
        )
        if aborted or absorbed:
            step["outcome"] = "unverified"
            step["reason"] = "lint_baseline_missing"
            step.pop("_diagnostics", None)
            deleted = f" (deleted: {', '.join(removed)})" if removed else ""
            warnings.append(
                f"`{step['command']}` not verified: the module has no checked-in lint baseline, so lint "
                f"wrote one{deleted} and gave no verdict"
            )
    return warnings


def git_status_paths(target: Path) -> list[str]:
    """Every modified, deleted, renamed or untracked path (ignored files excluded)."""
    result = subprocess.run(
        ["git", "-C", str(target), "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        text=True, capture_output=True, check=False,
    )
    parts = result.stdout.split("\0")
    paths: list[str] = []
    index = 0
    while index < len(parts):
        entry = parts[index]
        index += 1
        if len(entry) < 4:
            continue
        paths.append(entry[3:])
        if entry[0] in "RC":
            index += 1
    return paths


def touched_files(target: Path) -> set[str]:
    """Every file this change touches, whatever its type: the scope lint findings are judged against."""
    names = set(source_changes(target)) | set(git_status_paths(target))
    repo_map_path = cache_dir(target) / "repo-map.json"
    base = read_json(repo_map_path).get("base_commit") if repo_map_path.exists() else None
    diff = subprocess.run(
        ["git", "-C", str(target), "diff", "--name-only", *([base] if base else [])],
        text=True, capture_output=True, check=False,
    )
    names.update(line.strip() for line in diff.stdout.splitlines() if line.strip())
    return {name for name in names if ".ai" not in Path(name).parts}


def tree_state(target: Path) -> dict[str, str]:
    """Content hash of every path `git status` lists; a missing file hashes as deleted."""
    state: dict[str, str] = {}
    for name in git_status_paths(target):
        path = target / name
        state[name] = hashlib.sha1(path.read_bytes()).hexdigest() if path.is_file() else "<deleted>"
    return state


def apply_lint_scope(target: Path, config: dict[str, Any], steps: list[dict[str, Any]]) -> list[str]:
    """Waive lint and format failures whose findings all sit in files this change does not touch.

    Findings in a touched file still fail, whether or not they predate the branch. A failure whose
    findings cannot all be read (fewer parsed than the report announces, or none) also stays failed.
    `quality_gates.lint_scope: "all"` turns the waiver off. Each waiver is recorded, never silent.
    """
    if (config.get("quality_gates") or {}).get("lint_scope", "changed_files") != "changed_files":
        return []
    touched: set[str] | None = None
    warnings: list[str] = []
    for step in steps:
        if step["outcome"] != "failed" or failure_category(step["command"]) not in WAIVABLE_CATEGORIES:
            continue
        findings: list[tuple[str, str]] = step.get("_findings") or []
        expected = step.get("_expected_findings")
        if not findings or (expected is not None and expected > len(findings)):
            continue
        touched = touched_files(target) if touched is None else touched
        inside = [key for path, key in findings if path in touched]
        if inside:
            step["in_scope_findings"] = len(inside)
            continue
        files = sorted({path for path, _ in findings})
        step["outcome"] = "waived"
        step["waiver"] = {
            "reason": "findings_outside_change_set",
            "count": len(findings),
            "file_count": len(files),
            "files": files[:20],
        }
        warnings.append(
            f"`{step['command']}` waived: {len(findings)} finding(s) in {len(files)} file(s) this change does not touch"
        )
    return warnings


def auto_format(
    target: Path,
    config: dict[str, Any],
    modules: list[str],
    covered: bool,
    log_path: Path,
) -> tuple[dict[str, Any] | None, list[str]]:
    """Run the project's own formatter before the check, so formatting never costs a fix round.

    Only when Setup found or recorded a formatter that can also fix (`format_apply`) and a check
    (`format_check`); `quality_gates.auto_format: false` opts out. The run is scoped to the touched
    modules, and any file the formatter changes that this change did not already modify is restored.
    """
    commands = config.get("commands") or {}
    apply_command, check_command = commands.get("format_apply"), commands.get("format_check")
    if not apply_command or not check_command or (config.get("quality_gates") or {}).get("auto_format") is False:
        return None, []
    timeout = config["execution"]["command_timeout_seconds"]
    scoped = scoped_format_checks(apply_command, modules) if covered else [apply_command]
    before = tree_state(target)
    warnings: list[str] = []

    def run(commands_to_run: list[str]) -> dict[str, Any]:
        invocations = [gradle_invocation(command) for command in commands_to_run]
        if all(invocations):
            tasks = list(dict.fromkeys(task for _, names in invocations for task in names))  # type: ignore[misc]
            line = shlex.join([invocations[0][0], *tasks, "--continue", "--console=plain"])  # type: ignore[index]
            return execute_command(line, target, timeout, full_output=True)
        outputs: list[str] = []
        code = 0
        for command in commands_to_run:
            result = execute_command(command, target, timeout, full_output=True)
            outputs.extend(result.pop("output", []))
            code = code or result["exit_code"]
        return {"exit_code": code, "output": outputs, "duration_seconds": None}

    started = time.monotonic()
    result = run(scoped)
    output = result.pop("output", [])
    if result["exit_code"] != 0 and covered and set(MISSING_TASK_PATTERN.findall("\n".join(output))):
        warnings.append("formatter task missing in a touched module; formatted project-wide")
        result = run([apply_command])
        output = result.pop("output", [])
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("\n".join(output) + "\n", encoding="utf-8")
    if result["exit_code"] != 0:
        warnings.append("auto-format did not finish cleanly; the format check below decides")
    after = tree_state(target)
    formatted = sorted(name for name, digest in after.items() if name in before and before[name] != digest)
    restored: list[str] = []
    for name in sorted(after):
        if name not in before and git_tracked(target, name):
            subprocess.run(["git", "-C", str(target), "checkout", "--", name], check=False, capture_output=True)
            restored.append(name)
    info = {
        "command": " && ".join(scoped),
        "exit_code": result["exit_code"],
        "duration_seconds": round(time.monotonic() - started, 3),
        "formatted": formatted,
        "restored_outside_change": restored,
        "log": str(log_path),
    }
    if formatted:
        warnings.append(f"auto-format rewrote {len(formatted)} file(s) this change already modified")
    if restored:
        warnings.append(f"auto-format touched {len(restored)} file(s) outside this change; restored them")
    return info, warnings


def run_gate_unit(
    target: Path,
    config: dict[str, Any],
    unit: list[str],
    log_path: Path,
    fallbacks: dict[str, str],
) -> tuple[list[dict[str, Any]], list[str]]:
    timeout = config["execution"]["command_timeout_seconds"]
    invocation = gradle_invocation(unit[0])
    if not invocation:
        step = execute_command(unit[0], target, timeout)
        step["outcome"] = "passed" if step["exit_code"] == 0 else "failed"
        return [step], []
    before = untracked_baselines(target)
    steps, lines = run_gradle_batch(invocation[0], unit, target, timeout, log_path)
    warnings: list[str] = []
    missing = set(MISSING_TASK_PATTERN.findall("\n".join(lines)))
    scoped_tasks = {(gradle_invocation(command) or ("", [""]))[1][0] for command in unit if command in fallbacks}
    if missing and scoped_tasks and missing <= scoped_tasks:
        # A module without the formatter task: fall back to the project-wide formatter command.
        unit = list(dict.fromkeys(fallbacks.get(command, command) for command in unit))
        warnings.append(f"formatter task missing in {', '.join(sorted(missing))}; ran it project-wide")
        steps, lines = run_gradle_batch(invocation[0], unit, target, timeout, log_path)
    warnings.extend(settle_lint_baselines(target, steps, lines, before))
    warnings.extend(apply_lint_scope(target, config, steps))
    for step in steps:
        diagnostics = step.pop("_diagnostics", None)
        step.pop("_findings", None)
        step.pop("_expected_findings", None)
        if diagnostics:
            step["diagnostic_count"] = len(diagnostics)
    return steps, warnings


def gate_attempt(
    target: Path,
    config: dict[str, Any],
    change_set: dict[str, Any],
    needs_device: bool,
    attempt: int,
    run_number: int = 1,
    extras: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """One pass over the gate; stops after the first unit that has a real failure.

    The formatter fixes first when the project has one that can. Without that, the format check
    runs alone before the slow tasks, so a formatting failure returns in seconds, not after a build.
    """
    modules, covered = gate_scope(target, change_set)
    changes = source_changes(target)
    consumers, omitted = consumer_modules(target, config, modules, changes)
    commands = gate_commands(
        config, change_set, needs_device, modules, scope_format=covered, consumers=consumers,
    )
    format_check = config["commands"].get("format_check") or config["commands"].get("ktlint")
    fallbacks = {
        command: format_check
        for command in (scoped_format_checks(format_check, modules) if covered and format_check else [])
        if command != format_check
    }
    batch = config.get("quality_gates", {}).get("batch_gradle", True)
    stem = f"gate-run{run_number}-attempt{attempt}"
    warnings: list[str] = []
    if extras is not None and consumers:
        extras["consumer_modules"] = consumers
    if omitted:
        warnings.append(
            f"{len(omitted)} more consumer module(s) reference a changed declaration and were not gated: "
            + ", ".join(omitted)
        )
    spec_path = run_dir(target) / "ticket-spec.json"
    route = read_json(spec_path).get("route") or [] if spec_path.exists() else []
    if route and "T3" not in route and len(changes) > EXPRESS_MAX_FILES:
        warnings.append(
            f"no Planner ran (express route) but {len(changes)} files changed; express allows {EXPRESS_MAX_FILES}"
        )
    formatted, format_warnings = auto_format(
        target, config, modules, covered, run_dir(target) / f"{stem}-format.log",
    )
    warnings.extend(format_warnings)
    if extras is not None and formatted is not None:
        extras["auto_format"] = formatted
    format_commands = [command for command in commands if failure_category(command) == "format"]
    rest = [command for command in commands if command not in format_commands]
    units = (
        [*gate_units(format_commands, batch), *gate_units(rest, batch)]
        if formatted is None and format_commands and rest
        else gate_units(commands, batch)
    )
    steps: list[dict[str, Any]] = []
    docs = feature_docs_step(target, config, changes)
    if docs:
        # A stale doc fails the gate but does not stop the build: one round reports every problem.
        docs["attempt"] = attempt
        steps.append(docs)
    for index, unit in enumerate(units, 1):
        log_path = run_dir(target) / f"{stem}-{index}.log"
        unit_steps, unit_warnings = run_gate_unit(target, config, unit, log_path, fallbacks)
        for step in unit_steps:
            step["attempt"] = attempt
        steps.extend(unit_steps)
        warnings.extend(unit_warnings)
        if not gate_steps_pass(unit_steps):
            break
    return steps, warnings


def run_gate_rounds(
    target: Path,
    config: dict[str, Any],
    change_set: dict[str, Any],
    needs_device: bool,
    run_number: int = 1,
    extras: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[str], float]:
    """One gate attempt; a red gate goes back to the Implementer, not to a retry loop here."""
    started = time.monotonic()
    steps, warnings = gate_attempt(target, config, change_set, needs_device, 1, run_number, extras)
    return steps, warnings, round(time.monotonic() - started, 3)


def markdown_section_value(content: str, heading: str) -> str:
    marker = f"# {heading}"
    if marker not in content:
        return ""
    rest = content.split(marker, 1)[1]
    next_pos = rest.find("\n# ")
    body = rest if next_pos < 0 else rest[:next_pos]
    return body.strip()


def device_report_status(device: str) -> str:
    first = markdown_section_value(device, "Status").splitlines()
    return first[0].strip().lower() if first else ""


STUB_PR_MARKER = "See the diff and `implementation-notes.md`"


def is_stub_pr_body(content: str) -> bool:
    text = content.strip()
    return (not text) or STUB_PR_MARKER in text


def short_pr_body(spec: dict[str, Any]) -> str:
    ticket = spec.get("ticket") or {}
    title = str(ticket.get("title") or "Android change").strip()
    description = str(ticket.get("description") or "").strip()
    if description and description != title:
        return f"{title}\n\n{description}\n"
    return f"{title}\n"


# Line-anchored so prose like "the file is generated with buildSrc" is kept.
AGENT_ATTRIBUTION_PATTERN = re.compile(
    r"^\s*(?:"
    r"co-authored-by:\s*.*(?:claude|chatgpt|copilot|gemini|antigravity|cursor|bot|"
    r"cloud\s*code|codex|grok|anthropic|openai)"
    r"|(?:🤖\s*)?generated\s+with\s+\[?(?:claude|chatgpt|copilot|gemini|antigravity|"
    r"cursor|cloud\s*code|codex|grok)"
    r"|made(?:\s+with|-with:)\s+\[?(?:cloud\s*code|claude|chatgpt|copilot|gemini|cursor|codex)"
    r"|🤖\s*generated\s+with"
    r"|assisted-by:"
    r")",
    re.I | re.M,
)


def strip_agent_attribution(text: str) -> str:
    kept = [line for line in text.splitlines() if not AGENT_ATTRIBUTION_PATTERN.search(line)]
    cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
    return f"{cleaned}\n" if cleaned else ""


def write_pr_description(agent_dir: Path, content: str) -> None:
    (agent_dir / "pr-description.md").write_text(strip_agent_attribution(content), encoding="utf-8")


def write_delivery(agent_dir: Path, spec: dict[str, Any], state: dict[str, Any]) -> None:
    existing = (agent_dir / "pr-description.md").read_text(encoding="utf-8") if (agent_dir / "pr-description.md").exists() else ""
    if existing and not is_stub_pr_body(existing):
        write_pr_description(agent_dir, existing)
        return
    write_pr_description(agent_dir, short_pr_body(spec))


STAGE_LOG_DEDUPE_SECONDS = 15


def append_stage_log(
    agent_dir: Path,
    stage: str,
    status: str,
    actor: str = "cli",
    note: str = "",
    role: str | None = None,
) -> None:
    path = agent_dir / "stage-log.md"
    if not path.exists():
        path.write_text(
            "# Stage log\n\n| time | stage | status | actor | note |\n|---|---|---|---|---|\n",
            encoding="utf-8",
        )
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    safe_note = note.replace("|", "\\|").replace("\n", " ")
    row = f"| {role or stage_label(stage)} | {status} | {actor} | {safe_note} |"
    # The lock makes check-and-append atomic: two concurrent `start`s otherwise both pass the check.
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        handle.seek(0)
        # A repeated command (a retried `start`, a double `update-spec`) must not log the same event twice.
        for line in handle.read().splitlines()[-6:]:
            logged, _, rest = line[2:].partition(" ")
            try:
                age = (now - datetime.strptime(logged, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)).total_seconds()
            except ValueError:
                continue
            if rest.strip() == row and age <= STAGE_LOG_DEDUPE_SECONDS:
                return
        handle.write(f"| {stamp} {row}\n")


def changed_paths(target: Path) -> tuple[list[str], list[str]]:
    """Every path the change touched, of any type, and the tracked subset: (names, tracked)."""
    names: list[str] = []
    try:
        recorded = run_dir(target) / "t4-files.json"
    except FileNotFoundError:
        recorded = None
    if recorded and recorded.exists():
        names.extend(str(item) for item in read_json(recorded).get("files", []))
    repo_map_path = cache_dir(target) / "repo-map.json"
    base = read_json(repo_map_path).get("base_commit") if repo_map_path.exists() else None
    diff_cmd = ["git", "-C", str(target), "diff", "--name-only"]
    if base:
        diff_cmd.append(base)
    diff = subprocess.run(diff_cmd, text=True, capture_output=True, check=False)
    tracked = [line.strip() for line in diff.stdout.splitlines() if line.strip()]
    names.extend(tracked)
    status = subprocess.run(
        ["git", "-C", str(target), "status", "--porcelain"],
        text=True,
        capture_output=True,
        check=False,
    )
    for entry in status.stdout.splitlines():
        if len(entry) >= 4:
            names.append(entry[3:].strip().split(" -> ")[-1].strip('"'))
    return names, tracked


def source_changes(target: Path) -> list[str]:
    names, tracked = changed_paths(target)
    suffixes = {".kt", ".java", ".xml", ".kts", ".gradle"}
    changed: set[str] = set()
    for name in names:
        path = Path(name)
        if ".ai" in path.parts:
            continue
        # A lint baseline only counts when a tracked one changed; a new one is a gate side effect.
        if BASELINE_NAME_PATTERN.search(name) and name not in tracked:
            continue
        if path.suffix in suffixes:
            changed.add(name)
    return sorted(changed)


def source_fingerprint(target: Path) -> str:
    """Identity of the current source change; a gate, review or device verdict is only valid for it."""
    digest = hashlib.sha256()
    for name in source_changes(target):
        path = target / name
        digest.update(name.encode("utf-8") + b"\0")
        digest.update(path.read_bytes() if path.is_file() else b"<deleted>")
        digest.update(b"\0")
    return digest.hexdigest()[:16]


def implementation_errors(target: Path) -> list[str]:
    errors: list[str] = []
    notes = text_if_exists(run_dir(target) / "implementation-notes.md")
    if "Host has not implemented yet." in notes or not notes.strip():
        errors.append("implementation-notes.md is still the Implementer stub")
    if not source_changes(target):
        errors.append("no source diff (.kt/.java/.xml/.kts/.gradle) outside workflow artifact dirs")
    return errors


def update_spec(target: Path, args: argparse.Namespace) -> dict[str, Any]:
    """Let the Planner correct what the CLI guessed; the route is recomputed from the result."""
    agent_dir = run_dir(target)
    spec = read_json(agent_dir / "ticket-spec.json")
    if args.surfaces is not None:
        spec["surfaces"] = [item.strip() for item in args.surfaces.split(",") if item.strip()]
    if args.acceptance is not None:
        spec["acceptance_criteria"] = [item.strip() for item in args.acceptance.split("|") if item.strip()]
    for field in ("type", "complexity", "risk", "reproduction"):
        value = getattr(args, field)
        if value is not None:
            spec[field] = value
    spec["route"] = route_for(spec["type"], spec["complexity"], spec["risk"], spec["surfaces"])
    errors = validate_artifact("ticket-spec.json", spec)
    if errors:
        raise ValueError("; ".join(errors))
    write_json(agent_dir / "ticket-spec.json", spec)
    append_stage_log(agent_dir, "T1", "completed", "host", "ticket-spec.json updated by Planner")
    return spec


def ticket_from_flags(args: argparse.Namespace) -> dict[str, Any]:
    if args.ticket:
        return read_json(args.ticket)
    surfaces = [item.strip() for item in (args.surfaces or "").split(",") if item.strip()]
    return {
        "id": args.id,
        "title": args.title,
        "description": args.description or args.title or "",
        "type": args.type,
        "reproduction": args.reproduction,
        "acceptance_criteria": [
            item.strip() for item in (args.acceptance or "").split("|") if item.strip()
        ],
        "surfaces": surfaces,
        "complexity": args.complexity,
        "risk": args.risk,
        "business_questions": [],
    }


def run_quality_gate(target: Path) -> dict[str, Any]:
    agent_dir = run_dir(target)
    config = read_json(cache_dir(target) / "project-config.json")
    change_set = read_json(agent_dir / "change-set-map.json")
    spec = read_json(agent_dir / "ticket-spec.json")
    needs_device = "T7" in spec["route"]
    previous = read_json(agent_dir / "gate-report.json") if (agent_dir / "gate-report.json").exists() else {}
    prior_runs = list(previous.get("prior_runs") or [])
    if previous.get("status") == "failed":
        prior_runs.append(
            {
                "status": "failed",
                "steps": previous.get("steps") or [],
            }
        )
    run_number = int(previous.get("run") or 0) + 1
    changed_toolkit = toolkit_changes(agent_dir)
    if changed_toolkit:
        report = {
            "schema_version": 1,
            "status": "blocked",
            "run": run_number,
            "reason": "toolkit_modified_during_run",
            "toolkit_files": changed_toolkit,
            "fingerprint": source_fingerprint(target),
            "steps": [],
            "prohibitions_respected": False,
            "prior_runs": prior_runs[-3:],
        }
        write_json(agent_dir / "gate-report.json", report)
        return report
    extras: dict[str, Any] = {}
    steps, warnings, duration = run_gate_rounds(
        target, config, change_set, needs_device, run_number=run_number, extras=extras,
    )
    scan = secret_scan(target)
    gate_status = "passed" if gate_steps_pass(steps) and scan["status"] == "passed" else "failed"
    waivers = [
        {"command": step["command"], **step["waiver"]} for step in steps if step.get("waiver")
    ]
    units = [
        {"tasks": step["batch"], "duration_seconds": step["duration_seconds"], "log": step["log"]}
        for step in steps if step.get("batch")
    ]
    report = {
        "schema_version": 1,
        "status": gate_status,
        "run": run_number,
        "fingerprint": source_fingerprint(target),
        "steps": steps,
        "units": units,
        "waivers": waivers,
        "warnings": warnings,
        "duration_seconds": duration,
        "secret_scan": scan,
        "prior_runs": prior_runs[-3:],
        "prohibitions_respected": True,
        **extras,
    }
    write_json(agent_dir / "gate-report.json", report)
    return report


def load_state(target: Path) -> dict[str, Any]:
    return read_json(run_dir(target) / "run-state.json")


def save_state(target: Path, state: dict[str, Any], metrics: dict[str, Any] | None = None) -> None:
    state["updated_at"] = int(time.time())
    write_json(run_dir(target) / "run-state.json", state)
    if metrics is not None:
        write_json(run_dir(target) / "stage-metrics.json", metrics)


OPENING_STATUSES = frozenset({"started", "running"})


def open_clock(entry: dict[str, Any], now: float) -> None:
    """Mark when an agent began, so the office can count its time second by second."""
    entry.setdefault("running_since", round(now, 3))


def close_clock(entry: dict[str, Any], now: float) -> float | None:
    """Seconds since the matching `started`, or None when the stage was never opened."""
    since = entry.pop("running_since", None)
    return max(now - float(since), 0) if isinstance(since, (int, float)) else None


@contextlib.contextmanager
def run_lock(target: Path):
    """One writer at a time for a run's state files: parallel agents log at the same moment, and a
    read-modify-write without it loses one of the updates."""
    path = run_dir(target) / ".state.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def log_stage(*args: Any, **kwargs: Any) -> dict[str, Any]:
    target = args[0] if args else kwargs["target"]
    with run_lock(target):
        return _log_stage(*args, **kwargs)


def _log_stage(
    target: Path,
    stage: str,
    status: str,
    note: str = "",
    actor: str = "host",
    files: list[str] | None = None,
    tokens: int | None = None,
    seconds: float | None = None,
    wait_seconds: float | None = None,
    slice_id: str | None = None,
) -> dict[str, Any]:
    """Record a host stage by role name (or stage id).

    Without `seconds`, its time runs from the stage's `started` (else the gap since the last recorded
    event). `wait_seconds` is time the run spent waiting on a human inside that gap; it is kept apart
    and not counted as work. `slice_id` (Implementer only) records one Implementer of a team.
    """
    from android_workflow.team import normalize_slice, slice_role

    agent_dir = run_dir(target)
    stage, role = resolve_stage(stage)
    if slice_id is not None:
        if stage != "T4":
            raise ValueError("--slice is only for the Implementer")
        slice_id = normalize_slice(slice_id)
    shown = slice_role(slice_id) if slice_id else role
    append_stage_log(agent_dir, stage, status, actor, note, role=shown)
    if stage == "T4" and files:
        kept = [
            item
            for item in files
            if ".ai" not in Path(item).parts
        ]
        if kept and slice_id:
            # Parallel Implementers each report their own files: keep the union, never the last one.
            previous = read_json(agent_dir / "t4-files.json").get("files", []) if (agent_dir / "t4-files.json").exists() else []
            kept = list(dict.fromkeys([*previous, *kept]))
        if kept:
            write_json(agent_dir / "t4-files.json", {"files": kept})
    state = load_state(target)
    metrics = read_json(agent_dir / "stage-metrics.json")
    now = time.time()
    if slice_id:
        return _log_slice(target, agent_dir, state, metrics, slice_id, status, note, files, tokens, seconds, wait_seconds, now)
    state.setdefault("stages", {}).setdefault(stage, {})
    previous = str(state["stages"][stage].get("status") or "")
    state["stages"][stage]["status"] = status
    if note:
        state["stages"][stage]["note"] = note
    entry = metrics.setdefault("stages", {}).setdefault(stage, {})
    if status in OPENING_STATUSES:
        # A marker: the agent's time is measured from here to its `completed`, and the office counts it live.
        if status == "started":
            seconds = None
        if previous not in OPENING_STATUSES:
            entry.pop("running_since", None)  # a clock left open by a crashed round starts over
        open_clock(entry, now)
    else:
        since = close_clock(entry, now)
        if seconds is None:
            seconds = since if since is not None else (now - int(state["updated_at"]) if state.get("updated_at") else None)
    if wait_seconds and seconds is not None:
        seconds = max(seconds - wait_seconds, 0)
    record_stage_metrics(
        metrics, stage, status, seconds=seconds, tokens=tokens,
        attempts=0 if status in {"running", "awaiting_host", "started"} else 1,
    )
    if wait_seconds:
        entry = metrics["stages"][stage]
        entry["human_wait_seconds"] = round((entry.get("human_wait_seconds") or 0) + wait_seconds, 3)
    if status == "completed" and stage == "T4":
        state["status"] = "running"
        state["current_stage"] = "T5"
    elif status == "completed" and stage == "T4L" and team_integrated(agent_dir, state):
        # The Tech Lead's integration ends the team's implementation: the gate is next.
        state["stages"].setdefault("T4", {}).update({"status": "completed", "integrated": True})
        state["status"] = "running"
        state["current_stage"] = "T5"
    elif status == "completed" and stage in {"T5", "T6", "T7", "T8"} and state.get("status") != "completed":
        route = list((read_json(agent_dir / "ticket-spec.json").get("route") if (agent_dir / "ticket-spec.json").exists() else None) or STAGES)
        later = [item for item in route if item > stage and item in {"T6", "T7", "T8"}]
        if stage == "T8":
            state.update({"status": "completed", "current_stage": None})
        else:
            state.update({"status": "running", "current_stage": later[0] if later else "T8"})
    if status == "completed" and stage in {"T6", "T7"}:
        verdict = recorded_verdict(agent_dir, stage)
        state["stages"][stage].update({"verdict": verdict, "fingerprint": source_fingerprint(target)})
    save_state(target, state, metrics)
    if status == "completed" and tokens is None and role and role.lower() in AGENT_STAGES and stage != "T5":
        return {
            **state,
            "warnings": [f"{role} logged without --tokens: pass the total the agent's result reports"],
        }
    return state


def mark_gate_started(target: Path) -> None:
    """The gate can take minutes: show it working (and its clock running) while it does.

    Its pid is recorded, so a gate killed by a signal (a host's command timeout) is not shown as
    running forever."""
    agent_dir = run_dir(target)
    with run_lock(target):
        state = load_state(target)
        metrics = read_json(agent_dir / "stage-metrics.json")
        previous = (state.get("stages") or {}).get("T5") or {}
        state.setdefault("stages", {})["T5"] = {**previous, "status": "running", "pid": os.getpid()}
        entry = metrics.setdefault("stages", {}).setdefault("T5", {})
        entry.pop("running_since", None)
        open_clock(entry, time.time())
        append_stage_log(agent_dir, "T5", "started", "cli", "running the checks")
        save_state(target, state, metrics)
    refresh_office(target)


def mark_gate_interrupted(target: Path) -> None:
    """A gate that crashed or was stopped must not look like it is still running."""
    try:
        agent_dir = run_dir(target)
        with run_lock(target):
            state = load_state(target)
            metrics = read_json(agent_dir / "stage-metrics.json")
            state.setdefault("stages", {})["T5"] = {"status": "failed", "note": "the gate did not finish"}
            close_clock(metrics.setdefault("stages", {}).setdefault("T5", {}), time.time())
            append_stage_log(agent_dir, "T5", "failed", "cli", "the gate did not finish")
            save_state(target, state, metrics)
    except (OSError, ValueError):
        pass


def team_integrated(agent_dir: Path, state: dict[str, Any]) -> bool:
    """True once every slice of the team plan has completed and none is still working."""
    from android_workflow.team import read_plan

    plan = read_plan(agent_dir)
    if not plan or len(plan["slices"]) < 2:
        return False
    slices = ((state.get("stages") or {}).get("T4") or {}).get("slices") or {}
    return all((slices.get(item["id"]) or {}).get("status") == "completed" for item in plan["slices"])


def _log_slice(
    target: Path,
    agent_dir: Path,
    state: dict[str, Any],
    metrics: dict[str, Any],
    slice_id: str,
    status: str,
    note: str,
    files: list[str] | None,
    tokens: int | None,
    seconds: float | None,
    wait_seconds: float | None,
    now: float,
) -> dict[str, Any]:
    """One Implementer of a team. Its own time and tokens are kept per slice; the stage's wall time is
    the window in which any slice was working (parallel work is not counted twice), its tokens the sum."""
    from android_workflow.team import slice_role

    stage_state = state.setdefault("stages", {}).setdefault("T4", {})
    slices_state = stage_state.setdefault("slices", {})
    item = slices_state.setdefault(slice_id, {})
    item["status"] = status
    if note:
        item["note"] = note
    stage = metrics.setdefault("stages", {}).setdefault("T4", {})
    entry = stage.setdefault("slices", {}).setdefault(slice_id, {})
    if status in OPENING_STATUSES:
        open_clock(entry, now)
        open_clock(stage, now)
        own = None
    else:
        since = close_clock(entry, now)
        own = seconds if seconds is not None else since
        if wait_seconds and own is not None:
            own = max(own - wait_seconds, 0)
    entry["status"] = status
    if own is not None:
        entry["wall_time_seconds"] = round((entry.get("wall_time_seconds") or 0) + own, 3)
    else:
        entry.setdefault("wall_time_seconds", None)
    if tokens is not None:
        entry["tokens"] = (entry.get("tokens") or 0) + tokens
        stage["tokens"] = (stage.get("tokens") or 0) + tokens
    else:
        entry.setdefault("tokens", None)
        stage.setdefault("tokens", None)
    if status not in OPENING_STATUSES:
        entry["attempts"] = (entry.get("attempts") or 0) + 1
        if files:
            entry["files"] = list(dict.fromkeys([*(entry.get("files") or []), *[f for f in files if ".ai" not in Path(f).parts]]))
    still_working = any(
        (other.get("status") in OPENING_STATUSES) for name, other in slices_state.items() if name != slice_id
    )
    if status in OPENING_STATUSES:
        stage_state["status"] = "started"
        stage["status"] = "started"
    elif not still_working:
        window = close_clock(stage, now)
        if window is not None:
            stage["wall_time_seconds"] = round((stage.get("wall_time_seconds") or 0) + window, 3)
        stage["attempts"] = (stage.get("attempts") or 0) + 1
        stage["status"] = status
        if status == "completed" and stage_state.get("integrated"):
            # A fix round after the Tech Lead integrated: the change goes straight back to the gate.
            stage_state["status"] = "completed"
            state["status"] = "running"
            state["current_stage"] = "T5"
        else:
            # Every Implementer is done; the Tech Lead integrates their work before the gate.
            stage_state["status"] = "integrating" if status == "completed" else status
    stage.setdefault("wall_time_seconds", None)
    save_state(target, state, metrics)
    if status == "completed" and tokens is None:
        return {**state, "warnings": [f"{slice_role(slice_id)} logged without --tokens: pass the total the agent's result reports"]}
    return state


def recorded_verdict(agent_dir: Path, stage: str) -> str:
    """Read the Reviewer's or Device agent's verdict from its artifact."""
    if stage == "T6":
        path = agent_dir / "review.json"
        review = read_json(path) if path.exists() else {}
        if review.get("status") == "approved" and review.get("blocking"):
            return "changes_requested"
        return str(review.get("status") or "not_run")
    path = agent_dir / "device-report.md"
    status = device_report_status(path.read_text(encoding="utf-8")) if path.exists() else ""
    return {"passed": "pass", "failed": "fail"}.get(status, status or "not_run")


def media_files(agent_dir: Path, phase: str) -> list[Path]:
    folder = agent_dir / "media" / phase
    return sorted(path for path in folder.glob("*") if path.is_file()) if folder.is_dir() else []


def verification_errors(
    target: Path,
    spec: dict[str, Any],
    state: dict[str, Any],
    gate: dict[str, Any],
    skip_device: str | None,
) -> list[str]:
    """Every verdict must exist and belong to the current source, so a late fix cannot ship unverified."""
    agent_dir = run_dir(target)
    current = source_fingerprint(target)
    errors: list[str] = []
    if gate.get("status") == "passed" and gate.get("fingerprint") not in (None, current):
        errors.append("gate is stale (source changed after it passed); run `gate` again")
    review = state.get("stages", {}).get("T6", {})
    if recorded_verdict(agent_dir, "T6") != "approved":
        errors.append("review.json is not approved; run the Reviewer and `log --stage Reviewer`")
    elif review.get("fingerprint") != current:
        errors.append("review is missing or stale; re-review the change and `log --stage Reviewer`")
    if "T7" in spec["route"] and not skip_device:
        device = state.get("stages", {}).get("T7", {})
        if recorded_verdict(agent_dir, "T7") != "pass":
            errors.append("device-report.md is not PASS; run the Device stage and `log --stage Device`")
        elif device.get("fingerprint") != current:
            errors.append("device verdict is missing or stale; re-run the Device stage and `log --stage Device`")
        if "ui" in spec["surfaces"] and not media_files(agent_dir, "after"):
            errors.append("visual ticket has no media/after evidence")
    return errors


def finish_run(
    target: Path,
    skip_device: str | None = None,
    draft: str | None = None,
) -> dict[str, Any]:
    """Final check before the PR. With `draft`, unresolved checks become known issues of a draft PR."""
    errors = implementation_errors(target)
    if errors:
        raise ValueError("Implementer incomplete: " + "; ".join(errors))
    agent_dir = run_dir(target)
    spec = read_json(agent_dir / "ticket-spec.json")
    state = load_state(target)
    metrics = read_json(agent_dir / "stage-metrics.json")
    gate = read_json(agent_dir / "gate-report.json")
    issues: list[str] = []
    if gate.get("status") == "failed":
        issues.append("quality gate is failing; see gate-report.json")
    elif gate.get("status") == "blocked":
        issues.append("quality gate is blocked: " + (gate.get("reason") or "see gate-report.json"))
    elif gate.get("status") != "passed":
        issues.append("quality gate was not run")
    changed_toolkit = toolkit_changes(agent_dir)
    if changed_toolkit:
        issues.append(
            "the workflow toolkit changed during this run (" + ", ".join(changed_toolkit)
            + "); a human must review that change"
        )
    issues.extend(verification_errors(target, spec, state, gate, skip_device))
    if issues and not draft:
        raise ValueError(
            "not verified: " + "; ".join(issues)
            + ". Fix it, or `finish --draft \"<reason>\"` to open the PR as a draft with these issues listed"
        )
    state["draft"] = {"reason": draft, "issues": issues} if issues else None
    finish_started = time.monotonic()
    state["stages"]["T5"] = {"status": "completed" if gate.get("status") == "passed" else "escalated"}
    if "T7" not in spec["route"]:
        state["stages"]["T7"] = {"status": "skipped", "reason": "not_required"}
    elif skip_device:
        state["stages"]["T7"] = {"status": "skipped", "reason": skip_device}
        append_stage_log(agent_dir, "T7", "skipped", "host", skip_device)
    changed = source_changes(target)
    state["stages"]["T4"] = {**(state["stages"].get("T4") or {}), "status": "completed", "files": changed}
    state["stages"]["T8"] = {"status": "completed", "ready_for_review": not issues}
    state["stages"]["T9"] = {"status": "completed"}
    delivery_started = time.monotonic()
    write_delivery(agent_dir, spec, state)
    append_stage_log(agent_dir, "T8", "completed", "cli", "pr-description.md")
    metrics.setdefault("stages", {}).setdefault("T4", {}).update({"status": "completed", "files": changed})
    record_stage_metrics(metrics, "T8", "completed", seconds=time.monotonic() - delivery_started)
    state.update({
        "status": "completed", "current_stage": None, "pending_question": None,
        "finished_fingerprint": source_fingerprint(target),
    })
    if issues:
        append_stage_log(agent_dir, "T8", "draft", "host", f"{draft}: {len(issues)} known issue(s)")
    append_stage_log(agent_dir, "T9", "completed", "cli", "run completed")
    record_stage_metrics(metrics, "T9", "completed", seconds=time.monotonic() - finish_started)
    record_totals(metrics, state)
    save_state(target, state, metrics)
    return state


def status_payload(target: Path) -> dict[str, Any]:
    state = load_state(target)
    log_path = run_dir(target) / "stage-log.md"
    log_text = text_if_exists(log_path)
    tail = "\n".join(log_text.splitlines()[-12:])
    return {
        "status": state.get("status"),
        "current_stage": state.get("current_stage"),
        "current_role": STAGE_ROLES.get(state.get("current_stage") or "", state.get("current_stage")),
        "stages": state.get("stages"),
        "pending_question": state.get("pending_question"),
        "source_changes": source_changes(target),
        "implementation_errors": implementation_errors(target),
        "run_dir": str(run_dir(target)),
        "stage_log_path": str(log_path),
        "stage_log_tail": tail,
        "next": {
            "paused": "resume --question-id ... --answer ...",
            "awaiting_host": "write the change as Implementer, then `log --stage Implementer --status completed` and `gate`",
            "running": "continue the current stage",
            "escalated": "read current_stage and the reason in run-state.json",
            "completed": "nothing; the ticket already has implementation and delivery",
        }.get(state.get("status", ""), "status --target TARGET"),
    }


def execute_pipeline(target: Path, state: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    """Triage is done; locate the change set, check the plan precondition, then hand over to the host."""
    agent_dir = run_dir(target)
    repo_map = read_json(cache_dir(target) / "repo-map.json")
    metrics = read_json(agent_dir / "stage-metrics.json")
    state["stages"]["T1"] = {"status": "completed"}

    started = stage_start(state, "T2")
    change_set = locate_change_set(target, spec, repo_map)
    write_json(agent_dir / "change-set-map.json", change_set)
    append_stage_log(agent_dir, "T2", "completed", "cli", f"{len(change_set['candidate_files'])} candidate file(s)")
    stage_end(state, metrics, "T2", started)

    started = stage_start(state, "T3")
    if "T3" in spec["route"]:
        write_plan(agent_dir, spec, change_set)
        if spec["type"] == "bug" and not spec.get("reproduction"):
            stage_end(state, metrics, "T3", started, "escalated", reason="bug_reproduction_missing")
            state.update({"status": "escalated", "current_stage": "T3"})
            save_state(target, state, metrics)
            return state
        stage_end(state, metrics, "T3", started)
    else:
        stage_end(state, metrics, "T3", started, "skipped", reason="not_required")

    started = stage_start(state, "T4")
    append_stage_log(agent_dir, "T4", "awaiting_host", "cli", "host must implement source")
    stage_end(state, metrics, "T4", started, "awaiting_host", reason="host_must_implement")
    state.update({"status": "awaiting_host", "current_stage": "T4"})
    save_state(target, state, metrics)
    return state


def run(target: Path, ticket: dict[str, Any]) -> dict[str, Any]:
    started = time.monotonic()
    ensure_gitignore(target)
    bootstrap(target)
    bootstrap_seconds = time.monotonic() - started
    started = time.monotonic()
    spec = ticket_spec(ticket, target)
    ticket_id = ticket_slug(spec["ticket"].get("id"))
    agent_dir = run_dir(target, ticket_id)
    agent_dir.mkdir(parents=True, exist_ok=True)
    set_current(target, ticket_id)
    write_json(agent_dir / "ticket-spec.json", spec)
    write_json(agent_dir / TOOLKIT_FINGERPRINT_FILE, {"files": toolkit_fingerprint()})
    # Starting a ticket again is a new run: verdicts of the previous one must not carry over.
    for verdict in RUN_VERDICTS:
        (agent_dir / verdict).unlink(missing_ok=True)
    initialize_later_artifacts(agent_dir)
    # A new start is a new run: metrics from an earlier start of this ticket would be double-counted.
    metrics: dict[str, Any] = {"schema_version": 1, "stages": {}}
    record_stage_metrics(metrics, "T0", "completed", seconds=bootstrap_seconds)
    record_stage_metrics(metrics, "T1", "completed", seconds=time.monotonic() - started)
    write_json(agent_dir / "stage-metrics.json", metrics)
    state = {
        "schema_version": 1,
        "run_id": str(uuid.uuid4()),
        "status": "running",
        "current_stage": "T1",
        "stages": {"T0": {"status": "completed"}, "T1": {"status": "running"}},
        "pending_question": None,
        "answers": {},
        "created_at": int(time.time()),
        "updated_at": int(time.time()),
    }
    question = pending_question(spec, state["answers"])
    if question:
        state.update({"status": "paused", "pending_question": question})
        write_json(agent_dir / "run-state.json", state)
        return state
    return execute_pipeline(target, state, spec)


def resume(target: Path, question_id: str, answer: str) -> dict[str, Any]:
    agent_dir = run_dir(target)
    state_path = agent_dir / "run-state.json"
    state = read_json(state_path)
    if state["status"] != "paused":
        raise ValueError("the workflow is not paused")
    pending = state.get("pending_question") or {}
    if pending.get("id") != question_id:
        raise ValueError(f"pending question is {pending.get('id')!r}, not {question_id!r}")
    state["answers"][question_id] = answer
    spec = read_json(agent_dir / "ticket-spec.json")
    for question in spec["business_questions"]:
        if question["id"] == question_id:
            question["answer"] = answer
    write_json(agent_dir / "ticket-spec.json", spec)
    next_question = pending_question(spec, state["answers"])
    if next_question:
        state.update({"pending_question": next_question, "updated_at": int(time.time())})
        write_json(state_path, state)
        return state
    return execute_pipeline(target, state, spec)


def validate_artifact(name: str, value: dict[str, Any], schema_path: Path | None = None) -> list[str]:
    schema_path = schema_path or SKILL_DIR / "artifacts.schema.json"
    contracts = read_json(schema_path)["definitions"]
    contract = contracts.get(name)
    if not contract:
        return [f"unknown contract: {name}"]
    errors = [f"missing required field: {field}" for field in contract.get("required", []) if field not in value]
    for field, rules in contract.get("properties", {}).items():
        if field not in value:
            continue
        if "const" in rules and value[field] != rules["const"]:
            errors.append(f"{field} must be {rules['const']!r}")
        if "enum" in rules and value[field] not in rules["enum"]:
            errors.append(f"{field} is outside the enum")
    return errors


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="android-workflow")
    subparsers = parser.add_subparsers(dest="command", required=True)
    start_parser = subparsers.add_parser("start")
    start_parser.add_argument("--target", required=True, type=Path)
    start_parser.add_argument("--ticket", type=Path)
    start_parser.add_argument("--id")
    start_parser.add_argument("--title")
    start_parser.add_argument("--description", default="")
    start_parser.add_argument("--type", default="feature")
    start_parser.add_argument("--surfaces", default="")
    start_parser.add_argument("--complexity", default="low")
    start_parser.add_argument("--risk", default="low")
    start_parser.add_argument("--acceptance", default="")
    start_parser.add_argument("--reproduction")
    start_parser.add_argument("--overrides", type=Path)
    resume_parser = subparsers.add_parser("resume")
    resume_parser.add_argument("--target", required=True, type=Path)
    resume_parser.add_argument("--question-id", required=True)
    resume_parser.add_argument("--answer", required=True)
    status_parser = subparsers.add_parser("status")
    status_parser.add_argument("--target", required=True, type=Path)
    setup_parser = subparsers.add_parser("setup")
    setup_parser.add_argument("--target", required=True, type=Path)
    setup_parser.add_argument("--force", action="store_true", help="re-probe even if nothing changed")
    setup_parser.add_argument("--accept", action="store_true", help="record that the Setup agent verified the profile")
    setup_parser.add_argument("--source-repo", type=Path, help="main checkout whose setup a worktree may reuse")
    gate_parser = subparsers.add_parser("gate")
    gate_parser.add_argument("--target", required=True, type=Path)
    log_parser = subparsers.add_parser("log")
    log_parser.add_argument("--target", required=True, type=Path)
    log_parser.add_argument(
        "--stage", required=True,
        help="role name: Planner, Implementer, Quality gate, Reviewer, Device, Delivery",
    )
    log_parser.add_argument("--status", required=True, help="`started` when an agent begins, `completed` when it ends")
    log_parser.add_argument("--note", default="")
    log_parser.add_argument("--actor", default="host")
    log_parser.add_argument("--file", action="append", default=[], dest="files")
    log_parser.add_argument("--tokens", type=int, help="tokens the agent reported for this stage")
    log_parser.add_argument("--seconds", type=float, help="stage wall time, when the host measured it")
    log_parser.add_argument(
        "--wait-seconds", type=float, dest="wait_seconds",
        help="time inside this stage spent waiting on the human; excluded from the stage's work time",
    )
    log_parser.add_argument(
        "--slice", dest="slice_id",
        help="Implementer of a team: the slice id from team-plan.json (S1, S2, S3)",
    )
    finish_parser = subparsers.add_parser("finish")
    finish_parser.add_argument("--target", required=True, type=Path)
    finish_parser.add_argument(
        "--skip-device",
        metavar="REASON",
        help="finish without the required Device stage; the reason is logged",
    )
    finish_parser.add_argument(
        "--draft",
        metavar="REASON",
        help="the run stopped after the code was written: deliver a draft PR that lists what is unresolved",
    )
    prebuild_parser = subparsers.add_parser(
        "prebuild", help="build the base APK in the background while the Planner works",
    )
    prebuild_parser.add_argument("--target", required=True, type=Path)
    prebuild_parser.add_argument("--wait", action="store_true", help="block until the build ends")
    prebuild_parser.add_argument("--status", action="store_true", help="report without starting")
    prebuild_parser.add_argument("--force", action="store_true", help="build even if the route has no device stage")
    prebuild_parser.add_argument("--timeout", type=float, default=1800.0)
    deliver_parser = subparsers.add_parser(
        "deliver", help="commit, push and open the PR for a finished run (no agent needed)",
    )
    deliver_parser.add_argument("--target", required=True, type=Path)
    deliver_parser.add_argument("--subject", help="commit and PR title; default `<ticket id>: <ticket title>`")
    deliver_parser.add_argument("--body", help="up to 3 lines of commit body")
    deliver_parser.add_argument("--base")
    deliver_parser.add_argument("--no-commit", action="store_true")
    deliver_parser.add_argument("--no-push", action="store_true")
    deliver_parser.add_argument("--no-pr", action="store_true")
    spec_parser = subparsers.add_parser("update-spec")
    spec_parser.add_argument("--target", required=True, type=Path)
    spec_parser.add_argument("--surfaces")
    spec_parser.add_argument("--acceptance", help="criteria separated by |")
    spec_parser.add_argument("--type", choices=("bug", "feature", "chore"))
    spec_parser.add_argument("--complexity", choices=("low", "medium", "high"))
    spec_parser.add_argument("--risk", choices=("low", "medium", "high"))
    spec_parser.add_argument("--reproduction")
    list_parser = subparsers.add_parser("list")
    list_parser.add_argument("--target", required=True, type=Path)
    clean_parser = subparsers.add_parser("clean")
    clean_parser.add_argument("--target", required=True, type=Path)
    clean_parser.add_argument("--ticket", help="delete only this ticket's run; without it every run is deleted")
    clean_parser.add_argument(
        "--stale", nargs="?", type=float, const=24.0, metavar="HOURS",
        help="remove unfinished runs (awaiting_host/paused/running) idle for HOURS (default 24)",
    )
    skills_parser = subparsers.add_parser("skills", help="pick the Android skills each agent reads (RUN/skills.json)")
    skills_parser.add_argument("--target", required=True, type=Path)
    skills_parser.add_argument("--add", action="append", default=[], help="a skill the Planner wants read (repeatable)")
    skills_parser.add_argument("--drop", action="append", default=[], help="a skill that does not apply (repeatable)")
    team_parser = subparsers.add_parser("team", help="check the Tech Lead's team-plan.json, or who touched what")
    team_parser.add_argument("--target", required=True, type=Path)
    team_parser.add_argument("--check", action="store_true", help="after the Implementers: write team-report.json")
    emulator_parser = subparsers.add_parser(
        "emulator", help="start an existing emulator when no device is connected (and stop it at the end)",
    )
    emulator_parser.add_argument("--target", required=True, type=Path)
    emulator_parser.add_argument("--wait", action="store_true", help="block until the device has booted")
    emulator_parser.add_argument("--stop", action="store_true", help="close the emulator, only if the workflow started it")
    emulator_parser.add_argument("--status", action="store_true", help="report without starting anything")
    emulator_parser.add_argument("--avd", help="AVD to start (default: device.avd in the project overrides, else the first)")
    emulator_parser.add_argument("--headless", action="store_true", help="start without a window")
    emulator_parser.add_argument("--timeout", type=float, help="seconds --wait waits for the boot (default 300)")
    office_parser = subparsers.add_parser("office", help="open the page that shows the agents at work")
    office_parser.add_argument("--target", required=True, type=Path)
    office_parser.add_argument("--no-open", action="store_true", help="only print the page's path")
    evidence_parser = subparsers.add_parser("evidence")
    evidence_parser.add_argument("action", choices=("capture", "ingest", "compare", "list"))
    evidence_parser.add_argument("--target", required=True, type=Path)
    evidence_parser.add_argument("--phase", choices=("before", "after"), default="after")
    evidence_parser.add_argument("--name", default="screen")
    evidence_parser.add_argument("--kind", choices=("screenshot", "video"), default="screenshot")
    evidence_parser.add_argument("--seconds", type=int, default=8)
    evidence_parser.add_argument("--file", type=Path)
    return parser.parse_args(argv)


def _dispatch(args: argparse.Namespace) -> tuple[Any, int]:
    target = args.target.resolve()
    ensure_gitignore(target)
    if args.command == "start":
        if not args.ticket and not (args.id and args.title):
            raise ValueError("start requires --ticket or --id and --title")
        if args.overrides:
            configure(target, args.overrides)
        result = run(target, ticket_from_flags(args))
        from android_workflow.skills import refresh as refresh_skills

        refresh_skills(target)  # which Android skills this ticket needs, from the ticket and its likely files
        return result, 0
    if args.command == "resume":
        return resume(target, args.question_id, args.answer), 0
    if args.command == "log":
        result = log_stage(
            target, args.stage, args.status, args.note, args.actor, args.files, args.tokens, args.seconds,
            args.wait_seconds, args.slice_id,
        )
        if args.status == "started" and resolve_stage(args.stage)[0] == "T4":
            from android_workflow.skills import refresh as refresh_skills

            refresh_skills(target)  # an Implementer starts (or a fix round): the code it changed so far counts too
        return result, 0
    if args.command == "gate":
        mark_gate_started(target)
        import signal

        def stopped(signum: int, _frame: Any) -> None:
            raise SystemExit(128 + signum)  # a host timeout: unwind, so the gate is marked as not finished

        previous_handlers = {}
        for sig in (signal.SIGTERM, signal.SIGHUP):
            try:
                previous_handlers[sig] = signal.signal(sig, stopped)
            except ValueError:  # not the main thread: nothing to install
                pass
        try:
            report = run_quality_gate(target)
        except BaseException:
            mark_gate_interrupted(target)
            raise
        finally:
            for sig, handler in previous_handlers.items():
                signal.signal(sig, handler)
        with run_lock(target):
            state = load_state(target)
            metrics = read_json(run_dir(target) / "stage-metrics.json")
            status = "completed" if report["status"] == "passed" else "escalated"
            state["stages"]["T5"] = {"status": status, "gate": report["status"]}
            since = close_clock(metrics.setdefault("stages", {}).setdefault("T5", {}), time.time())
            record_stage_metrics(
                metrics, "T5", status,
                seconds=report.get("duration_seconds") if report.get("duration_seconds") is not None else since,
            )
            if status == "escalated":
                state.update({"status": "escalated", "current_stage": "T5"})
            else:
                state.update({"status": "running", "current_stage": "T6"})
            notes = [report["status"]]
            if report.get("reason"):
                notes.append(report["reason"])
            if report.get("waivers"):
                notes.append(f"{len(report['waivers'])} lint step(s) waived: findings only in untouched files")
            formatted = (report.get("auto_format") or {}).get("formatted")
            if formatted:
                notes.append(f"auto-formatted {len(formatted)} file(s)")
            append_stage_log(run_dir(target), "T5", status, "cli", "; ".join(notes))
            save_state(target, state, metrics)
        return {"gate": report, "run": state}, 0 if status == "completed" else 1
    if args.command == "status":
        return status_payload(target), 0
    if args.command == "finish":
        with run_lock(target):
            return finish_run(target, skip_device=args.skip_device, draft=args.draft), 0
    if args.command == "prebuild":
        from android_workflow.prebuild import current_status, start_prebuild, wait_prebuild

        if args.status:
            return current_status(target), 0
        record = start_prebuild(target, force=args.force)
        if args.wait and record.get("status") == "running":
            record = wait_prebuild(target, timeout=args.timeout)
        return record, 0 if record.get("status") in {"running", "passed", "skipped"} else 1
    if args.command == "deliver":
        from android_workflow.delivery import deliver

        return deliver(
            target, subject=args.subject, body=args.body, base=args.base,
            no_commit=args.no_commit, no_push=args.no_push, no_pr=args.no_pr,
        ), 0
    if args.command == "setup":
        result = run_setup(target, force=args.force, accept=args.accept, source_repo=args.source_repo)
        return result, 0
    if args.command == "update-spec":
        result = update_spec(target, args)
        from android_workflow.skills import refresh as refresh_skills

        refresh_skills(target)
        return result, 0
    if args.command == "list":
        runs = list_runs(target)
        current = next((item["ticket_id"] for item in runs if item["current"]), None)
        return {"runs": runs, "current": current}, 0
    if args.command == "clean":
        from android_workflow.live import stop_watcher

        if not args.ticket and args.stale is None:
            from android_workflow.emulator import stop as stop_emulator

            stop_watcher(target)  # every run goes: the office has nothing left to watch
            stop_emulator(target)  # only one the workflow started; its record lives in the cache
        return clean_runs(target, ticket_id=args.ticket, stale_hours=args.stale), 0
    if args.command == "office":
        import webbrowser

        from android_workflow.office import render

        path = render(target)
        if not args.no_open:
            webbrowser.open(path.as_uri())
        return {"office": str(path)}, 0
    if args.command == "skills":
        from android_workflow.skills import select

        return select(target, add=args.add, drop=args.drop), 0
    if args.command == "team":
        from android_workflow.team import check_plan, team_report

        if args.check:
            return team_report(target), 0
        return check_plan(target)
    if args.command == "emulator":
        from android_workflow.emulator import dispatch_emulator

        return dispatch_emulator(target, args)
    if args.command == "evidence":
        from android_workflow.evidence import dispatch_evidence

        config_path = cache_dir(target) / "project-config.json"
        config = read_json(config_path) if config_path.exists() else None
        result = dispatch_evidence(target, args, config)
        if args.action in {"capture", "ingest"}:
            append_stage_log(run_dir(target), "T7", "completed", "cli", f"evidence {args.action} {args.name}")
        return result, 0
    raise ValueError(f"unknown command: {args.command}")


def refresh_office(target: Path) -> None:
    """Keep the office page in step with the run; a page problem never fails a workflow command.

    While the run is live, a background watcher keeps the page current between commands too.
    """
    from android_workflow.live import ensure_watcher
    from android_workflow.office import render

    try:
        if (target / ".ai" / "workflow").is_dir():
            render(target)
            ensure_watcher(target)
    except Exception:  # noqa: BLE001 - the page is a convenience, never a gate
        pass


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result, code = _dispatch(args)
        refresh_office(args.target.resolve())
        print(json.dumps(role_view(result), indent=2, ensure_ascii=False))
        return code
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
