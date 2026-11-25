"""How much the workflow decides on its own: the `autonomy` section of `.ai/android-workflow.json`.

A `level` picks a preset; any key set next to it wins over the preset. The developer's own file
(`~/.config/android-workflow/config.json`, or `$ANDROID_WORKFLOW_CONFIG`) sets their default for every
project; a project's `.ai/android-workflow.json` wins over it; `start --autonomy LEVEL` wins for one run.

  questions       ask | default   a Planner question that names its default: ask, or take the default
                                  and say so in the PR (a question with no default always asks)
  partial_device  ask | auto      a PARTIAL device check: ask, or decide by code (every AC proved: PR
                                  ready with what the device did not verify; an AC nothing proved: draft)
  critical        ask | draft     a partial device check on a critical change (risk high, payments,
                                  sign-in, `critical_words`): ask, or open a draft PR naming what is open
  critical_words  [words]         more words that make a ticket critical, besides the built-in ones
  infra           ask | draft     the gate cannot run (Gradle failed twice, disk full) or no device boots:
                                  ask, or open a draft PR that says so
  device_wait     ask | auto      no device after `device.wait_max_seconds`: ask, or prove the static AC
                                  with JVM renders when they can, else a draft PR
  open_pr         auto | ask      open the PR once the run passes, or ask first (`CLI approve deliver`)
  merge           auto | ask      the PR Watcher merges an approved, green PR, or tells the developer
                                  (`watch.auto_merge`, when set, wins)

What no level changes: credentials never leave the machine, nothing is force-pushed, hooks are never
skipped, a PR round pushes nothing the developer did not see, and reviewing someone else's PR keeps
its own rules.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

LEVELS = ("careful", "balanced", "autonomous")
DEFAULT_LEVEL = "balanced"
CHOICES: dict[str, tuple[str, ...]] = {
    "questions": ("ask", "default"),
    "partial_device": ("ask", "auto"),
    "critical": ("ask", "draft"),
    "infra": ("ask", "draft"),
    "device_wait": ("ask", "auto"),
    "open_pr": ("auto", "ask"),
    "merge": ("auto", "ask"),
}
PRESETS: dict[str, dict[str, str]] = {
    # Every decision that is not plain mechanics goes to the developer, and a human merges.
    "careful": {"questions": "ask", "partial_device": "ask", "critical": "ask", "infra": "ask",
                "device_wait": "ask", "open_pr": "ask", "merge": "ask"},
    # The default: what the workflow did before levels existed.
    "balanced": {"questions": "ask", "partial_device": "auto", "critical": "ask", "infra": "ask",
                 "device_wait": "ask", "open_pr": "auto", "merge": "auto"},
    # Asks only what it cannot answer (a question with no default, a phone's USB prompt); anything
    # it cannot prove opens as a draft PR instead of a question.
    "autonomous": {"questions": "default", "partial_device": "auto", "critical": "draft", "infra": "draft",
                   "device_wait": "auto", "open_pr": "auto", "merge": "auto"},
}


def user_config_path() -> Path:
    configured = os.environ.get("ANDROID_WORKFLOW_CONFIG")
    return Path(configured).expanduser() if configured else Path.home() / ".config" / "android-workflow" / "config.json"


def _section(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    section = data.get("autonomy") if isinstance(data, dict) else None
    return section if isinstance(section, dict) else {}


def _run_level(target: Path) -> str | None:
    from android_workflow.paths import run_dir

    try:
        state = json.loads((run_dir(target) / "run-state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, RuntimeError):
        return None
    level = state.get("autonomy")
    return level if level in LEVELS else None


def effective(target: Path, run_level: str | None = None) -> dict[str, Any]:
    """The policy for this target, each key with where it came from (`sources`). Unknown keys and
    values are ignored (`ignored` lists them) so a typo never turns a question into an action."""
    user, project = _section(user_config_path()), _section(Path(target) / ".ai" / "android-workflow.json")
    run_level = run_level or _run_level(Path(target))
    level, level_from = DEFAULT_LEVEL, "default"
    for source, section in (("user", user), ("project", project)):
        if section.get("level") in LEVELS:
            level, level_from = section["level"], source
    if run_level in LEVELS:
        level, level_from = run_level, "run"
    policy: dict[str, Any] = {"level": level}
    sources: dict[str, str] = {"level": level_from}
    for key, value in PRESETS[level].items():
        policy[key], sources[key] = value, f"level {level}"
    ignored: list[str] = []
    for source, section in (("user", user), ("project", project)):
        for key, value in section.items():
            if key == "level":
                if value not in LEVELS:
                    ignored.append(f"{source}: level={value!r}")
                continue
            if key == "critical_words":
                if isinstance(value, list):
                    words = [str(item).lower() for item in value if str(item).strip()]
                    policy["critical_words"] = sorted({*policy.get("critical_words", []), *words})
                    sources["critical_words"] = source
                else:
                    ignored.append(f"{source}: critical_words must be a list")
            elif key in CHOICES and value in CHOICES[key]:
                policy[key], sources[key] = value, source
            else:
                ignored.append(f"{source}: {key}={value!r}")
    if run_level in LEVELS:  # `start --autonomy`: the developer's word for this run wins over the files
        for key, value in PRESETS[run_level].items():
            policy[key], sources[key] = value, "run"
    policy.setdefault("critical_words", [])
    out = {**policy, "sources": sources}
    if ignored:
        out["ignored"] = ignored
    return out


def get(target: Path, key: str) -> Any:
    return effective(target)[key]


def take_default(question: str) -> str | None:
    """The default a Planner question names (`Which label? (default: Star)`), else None."""
    marker = "(default:"
    start = question.lower().rfind(marker)
    if start < 0:
        return None
    end = question.find(")", start)
    value = question[start + len(marker):end if end > start else None].strip()
    return value or None
