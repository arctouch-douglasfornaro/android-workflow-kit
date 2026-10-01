#!/usr/bin/env python3
"""Cheap filesystem facts for workflow-setup.

Prints markdown tables the project-explorer pastes into `.ai/project-profile.md`.
Judgment (which module is *the* app, which lint task is the real one) stays with
the agent — this script only reports what it can prove with a regex.

Does not execute Gradle. Does not network.
"""
from __future__ import annotations

import argparse
import fnmatch
import glob
import os
import re
import sys

PLUGIN_HINTS = (
    ("compose", re.compile(r"org\.jetbrains\.kotlin\.plugin\.compose|org\.jetbrains\.compose|compose\.compiler|androidx\.compose", re.I)),
    ("hilt", re.compile(r"com\.google\.dagger\.hilt|dagger\.hilt", re.I)),
    ("koin", re.compile(r"io\.insert-koin|org\.koin", re.I)),
    ("dagger", re.compile(r"com\.google\.dagger(?!\.hilt)|\"dagger\"", re.I)),
    ("kotlinter", re.compile(r"org\.jmailen\.kotlinter|jmailen\.gradle.*kotlinter", re.I)),
    ("ktlint-gradle", re.compile(r"org\.jlleitschuh\.gradle\.ktlint", re.I)),
    ("spotless", re.compile(r"com\.diffplug\.spotless", re.I)),
    ("detekt", re.compile(r"io\.gitlab\.arturbosch\.detekt", re.I)),
    ("ksp", re.compile(r"com\.google\.devtools\.ksp", re.I)),
)

DEP_HINTS = (
    ("mockk", re.compile(r"io\.mockk", re.I)),
    ("mockito", re.compile(r"org\.mockito", re.I)),
    ("turbine", re.compile(r"app\.cash\.turbine", re.I)),
    ("junit5", re.compile(r"org\.junit\.jupiter", re.I)),
    ("junit4", re.compile(r"junit:junit|org\.junit\.junit", re.I)),
    ("roborazzi", re.compile(r"io\.github\.takahirom\.roborazzi", re.I)),
    ("paparazzi", re.compile(r"app\.cash\.paparazzi", re.I)),
    ("compose-screenshot", re.compile(r"com\.android\.compose\.screenshot|compose\.screenshot", re.I)),
    ("navigation-compose", re.compile(r"androidx\.navigation:navigation-compose|navigation-compose", re.I)),
    ("navigation-3", re.compile(r"androidx\.navigation3|navigation3", re.I)),
)

INCLUDE_RE = re.compile(
    r"""include\s*\(\s*["'](:?[^"']+)["']""",
    re.I,
)
APPLICATION_ID_RE = re.compile(
    r"""applicationId\s*=\s*["']([^"']+)["']""",
)
NAMESPACE_RE = re.compile(
    r"""namespace\s*=\s*["']([^"']+)["']""",
)
# Both Gradle dialects: Kotlin/Groovy assignment (`minSdk = 24`) and the legacy space-separated
# Groovy form with the `Version` suffix (`minSdkVersion 24`). Requiring `=` reported `none` on any
# repo using the legacy form, and the explorer then wrote a profile with no SDK row for a project
# that plainly declares one.
SDK_RE = re.compile(
    r"\b(minSdk|compileSdk|targetSdk)(?:Version|Preview)?\s*(?:=\s*|\s+)(\d+)",
)


def read_text(path: str, limit: int = 200_000) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read(limit)
    except OSError:
        return ""


def find_first(root: str, names: tuple[str, ...]) -> str | None:
    for name in names:
        path = os.path.join(root, name)
        if os.path.isfile(path):
            return os.path.relpath(path, root)
    return None


def in_build_output(path: str, root: str) -> bool:
    """True when `path` sits inside a Gradle `build/` output dir *within the repo*.

    Must be judged on the path RELATIVE to the repo root. Testing the absolute path means a checkout
    under any directory called `build` -- a common CI workspace layout such as
    `/home/ci/build/repo` -- discards every Gradle file in the project, so the probe concludes
    `android_gradle: no` and the kit halts on a perfectly valid Android repo.
    """
    rel = os.path.relpath(path, root).replace("\\", "/")
    return rel == "build" or rel.startswith("build/") or "/build/" in rel


PRUNE_DIRS = {"build", ".git", ".gradle", ".idea", "node_modules", ".ai", ".kotlin", "out"}


def scan_tree(root: str) -> dict[str, list[str]]:
    """One pruned walk collecting every file set the probe needs, sorted.

    Previously each of `gradle_files`, `find_application_ids`, `find_sdk`, `find_launcher` and
    `feature_doc_candidates` ran its own `glob.glob(recursive=True)` over the whole tree -- nine
    full walks, and `build/` output was filtered only *after* being traversed. Measured on a large
    repo that was 60-120 s, which Phase 0.3 experiences as a hang.

    Pruning happens in-place on `dirnames`, so Gradle output and VCS metadata are never descended
    into at all. Results are **sorted**, because the "first wins" rows (SDK, applicationId,
    launcher) previously depended on filesystem enumeration order and could differ between runs on
    the same tree.
    """
    found: dict[str, list[str]] = {"gradle": [], "manifest": [], "markdown": []}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in PRUNE_DIRS and not d.startswith("."))
        for name in filenames:
            path = os.path.join(dirpath, name)
            if name in ("build.gradle", "build.gradle.kts", "settings.gradle",
                        "settings.gradle.kts", "libs.versions.toml"):
                found["gradle"].append(path)
            elif name == "AndroidManifest.xml":
                found["manifest"].append(path)
            elif name.endswith(".md"):
                found["markdown"].append(path)
    for key in found:
        found[key].sort()
    return found


def gradle_files(root: str, scanned: dict[str, list[str]] | None = None) -> list[str]:
    paths = (scanned or scan_tree(root))["gradle"]
    return sorted(p for p in paths if not in_build_output(p, root))


def parse_includes(settings_text: str) -> list[str]:
    modules = []
    for m in INCLUDE_RE.finditer(settings_text):
        raw = m.group(1).strip()
        if not raw.startswith(":"):
            raw = ":" + raw.replace("/", ":")
        modules.append(raw)
    # include(":a", ":b") — INCLUDE_RE already gets each quoted token if we also scan:
    for m in re.finditer(r'["\'](:[A-Za-z0-9_:\-]+)["\']', settings_text):
        if m.group(1) not in modules:
            modules.append(m.group(1))
    return sorted(set(modules))


def scan_hints(texts: list[str], hints: tuple) -> list[str]:
    blob = "\n".join(texts)
    found = []
    for name, rx in hints:
        if rx.search(blob):
            found.append(name)
    return found


def find_application_ids(root: str, scanned: dict[str, list[str]] | None = None) -> list[str]:
    ids = []
    for path in (scanned or scan_tree(root))["gradle"]:
        if in_build_output(path, root) or "libs.versions.toml" in path:
            continue
        text = read_text(path)
        for rx in (APPLICATION_ID_RE, NAMESPACE_RE):
            for m in rx.finditer(text):
                ids.append(f"{os.path.relpath(path, root)} → {m.group(1)}")
    return ids


def find_sdk(root: str, scanned: dict[str, list[str]] | None = None) -> dict[str, str]:
    # Sorted input, so "first wins" is deterministic instead of depending on directory order.
    found: dict[str, str] = {}
    for path in (scanned or scan_tree(root))["gradle"]:
        if in_build_output(path, root) or "libs.versions.toml" in path:
            continue
        for m in SDK_RE.finditer(read_text(path)):
            found.setdefault(m.group(1), m.group(2))
    return found


def find_launcher(root: str, scanned: dict[str, list[str]] | None = None) -> list[str]:
    hits = []
    for path in (scanned or scan_tree(root))["manifest"]:
        if in_build_output(path, root):
            continue
        text = read_text(path)
        if "android.intent.action.MAIN" in text and "android.intent.category.LAUNCHER" in text:
            hits.append(os.path.relpath(path, root))
    return hits


FEATURE_GLOBS = (
    "features/**/README.md",
    "feature/**/README.md",
    "docs/features/**/*.md",
)


def _matches_feature_glob(rel: str, pattern: str) -> bool:
    """Segment-aware glob so `**` spans any number of directories and `*` spans none.

    Plain `fnmatch` gives `**` no special meaning and lets `*` cross `/`, so
    `features/**/README.md` never matched a single-level `features/README.md` while
    `features/*.md` would wrongly match a deeply nested file.
    """
    parts, pat = rel.split("/"), pattern.split("/")

    def walk(pi: int, si: int) -> bool:
        while pi < len(pat):
            if pat[pi] == "**":
                if pi + 1 == len(pat):
                    return True
                return any(walk(pi + 1, s) for s in range(si, len(parts) + 1))
            if si >= len(parts) or not fnmatch.fnmatch(parts[si], pat[pi]):
                return False
            pi += 1
            si += 1
        return si == len(parts)

    return walk(0, 0)


def feature_doc_candidates(root: str, scanned: dict[str, list[str]] | None = None) -> list[tuple[str, str]]:
    """(relpath, reason) for files that *look* like feature overviews."""
    rows = []
    for path in (scanned or scan_tree(root))["markdown"]:
        if in_build_output(path, root):
            continue
        rel = os.path.relpath(path, root)
        if any(_matches_feature_glob(rel, g) for g in FEATURE_GLOBS):
            head = read_text(path, 4000)
            reason = []
            if head.lstrip().startswith("---") and "covers:" in head[:800]:
                reason.append("frontmatter covers:")
            if re.search(r"^## (Entry points|Architecture|Feature gating)\s*$", head, re.M | re.I):
                reason.append("overview headings")
            if not reason:
                reason.append("path match only — not treated as overview unless explorer agrees")
            rows.append((rel, ", ".join(reason)))
    return sorted(rows)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo-root", default=os.getcwd())
    args = p.parse_args(argv)
    root = os.path.abspath(args.repo_root)

    settings = find_first(root, ("settings.gradle.kts", "settings.gradle"))
    wrapper = find_first(root, ("gradlew",))
    agents = find_first(root, ("AGENTS.md",))
    scanned = scan_tree(root)
    files = gradle_files(root, scanned)
    texts = [read_text(f) for f in files]
    settings_text = read_text(os.path.join(root, settings)) if settings else ""

    modules = parse_includes(settings_text) if settings_text else []
    plugins = scan_hints(texts, PLUGIN_HINTS)
    deps = scan_hints(texts, DEP_HINTS)
    app_ids = find_application_ids(root, scanned)
    sdks = find_sdk(root, scanned)
    launchers = find_launcher(root, scanned)
    docs = feature_doc_candidates(root, scanned)

    gradle_props_text = read_text(os.path.join(root, "gradle.properties"))
    parallel_enabled = bool(re.search(r"org\.gradle\.parallel\s*=\s*true", gradle_props_text, re.I))
    cache_enabled = bool(re.search(r"org\.gradle\.caching\s*=\s*true", gradle_props_text, re.I))

    is_android = bool(settings) and any(
        re.search(r"com\.android\.(application|library)", t) for t in texts
    )

    lines = [
        "# android_probe",
        "",
        "## Layout",
        f"- repo_root: `{root}`",
        f"- android_gradle: {'yes' if is_android else 'no'}",
        f"- settings: `{settings or 'none'}`",
        f"- wrapper: `{('./' + wrapper) if wrapper else 'none'}`",
        f"- AGENTS.md: `{agents or 'none'}`",
        "",
        "## Modules (from settings include)",
        "| module |",
        "|---|",
    ]
    if modules:
        for m in modules:
            lines.append(f"| `{m}` |")
    else:
        lines.append("| _(none parsed)_ |")

    lines += [
        "",
        "## Plugin / toolchain hints",
        ", ".join(f"`{x}`" for x in plugins) or "none",
        "",
        "## Test library hints",
        ", ".join(f"`{x}`" for x in deps) or "none",
        "",
        "## Build performance hints",
        f"- org.gradle.parallel: {'true' if parallel_enabled else 'false / unconfigured'}",
        f"- org.gradle.caching: {'true' if cache_enabled else 'false / unconfigured'}",
        "",
        "## applicationId / namespace",
    ]
    if app_ids:
        lines.extend(f"- `{x}`" for x in app_ids)
    else:
        lines.append("- none")

    lines += ["", "## SDK (first wins)", ]
    if sdks:
        for k, v in sdks.items():
            lines.append(f"- {k}: {v}")
    else:
        lines.append("- none")

    lines += ["", "## Manifests with MAIN/LAUNCHER", ]
    if launchers:
        for x in launchers:
            lines.append(f"- `{x}`")
    else:
        lines.append("- none")

    lines += [
        "",
        "## Feature-doc candidates",
        "| path | reason |",
        "|---|---|",
    ]
    if docs:
        for path, reason in docs:
            lines.append(f"| `{path}` | {reason} |")
    else:
        lines.append("| _(none)_ | |")
        lines.append("")
        lines.append("feature_doc_glob: none")

    recognized = [d for d in docs if "path match only" not in d[1]]
    if recognized:
        # common prefix glob guess
        lines.append("")
        lines.append("feature_doc_glob: (explorer decides — candidates with overview signals listed above)")
    elif not docs:
        pass
    else:
        lines.append("")
        lines.append("feature_doc_glob: none  # path-only matches; explorer should leave empty unless a human says otherwise")

    sys.stdout.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
