#!/usr/bin/env python3
"""Release notes helper for the release-notes skill. Standard library only, read-only.

Default: print the project version, the baseline commit and the feat/fix commits since it.
  python3 release_changes.py [--since <ref|YYYY-MM-DD>] [--all]
--check <notes.json>: check What's New (<= 500, Google Play) and Promotional Text (<= 170, App Store)
for every locale in {"en-US": {"whatsNew": "...", "promotionalText": "..."}, ...}.
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

LIMITS = {"whatsNew": 500, "promotionalText": 170}
METADATA_PATHS = ["STORE_METADATA.json", "fastlane/metadata", "metadata", "store"]


def git(*args):
    out = subprocess.run(["git", *args], capture_output=True, text=True)
    return out.stdout.strip() if out.returncode == 0 else ""


def project_version():
    found = []
    for gradle in list(Path(".").glob("*/build.gradle.kts")) + list(Path(".").glob("*/build.gradle")):
        text = gradle.read_text(errors="ignore")
        name = re.search(r'versionName\s*=?\s*"([^"]+)"', text)
        code = re.search(r"versionCode\s*=?\s*(\d+)", text)
        if name:
            found.append(f"Android {name.group(1)} ({code.group(1) if code else '?'}) in {gradle}")
    skip = ("build", "Pods", "SourcePackages", "DerivedData", "node_modules", ".gradle")
    for pbx in Path(".").glob("**/*.xcodeproj/project.pbxproj"):
        if any(part in skip or part.startswith(".") for part in pbx.parts[:-1]):
            continue
        text = pbx.read_text(errors="ignore")
        name = re.search(r"MARKETING_VERSION = ([^;]+);", text)
        code = re.search(r"CURRENT_PROJECT_VERSION = ([^;]+);", text)
        if name:
            found.append(f"iOS {name.group(1)} ({code.group(1) if code else '?'}) in {pbx}")
    return found


def baseline(since):
    if since:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", since):
            ref = git("rev-list", "-1", f"--before={since}", "HEAD")
            return ref, f"last commit before {since}"
        return since, "given with --since"
    existing = [p for p in METADATA_PATHS if Path(p).exists()]
    if existing:
        ref = git("log", "-1", "--format=%H", "--", *existing)
        if ref:
            return ref, f"last change to the store texts ({', '.join(existing)})"
    tag = git("describe", "--tags", "--abbrev=0")
    if tag:
        return tag, "latest tag"
    return "HEAD~30", "no store file or tag: last 30 commits"


def changes(since, show_all):
    ref, why = baseline(since)
    print("Version:", "; ".join(project_version()) or "not found")
    label = git("log", "-1", "--format=%h %ad %s", "--date=short", ref) or ref
    print(f"Baseline: {label}  ({why})")
    log = git("log", "--reverse", "--no-merges", "--format=%h %ad %s", "--date=short", f"{ref}..HEAD")
    lines = [l for l in log.splitlines() if l]
    if not show_all:
        lines = [l for l in lines if re.search(r"\s(feat|fix)(\(|:|!)", l)]
    print(f"Commits ({'all' if show_all else 'feat/fix'}): {len(lines)}")
    for line in lines:
        print("  " + line)


def check(path):
    notes = json.loads(Path(path).read_text(encoding="utf-8"))
    ok = True
    for locale, texts in notes.items():
        for field, limit in LIMITS.items():
            text = texts.get(field)
            if text is None:
                print(f"{locale:7} {field:16} MISSING")
                ok = False
                continue
            size = len(text)
            state = "ok" if size <= limit else f"TOO LONG by {size - limit}"
            ok &= size <= limit
            print(f"{locale:7} {field:16} {size:4}/{limit}  {state}")
    return 0 if ok else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--since")
    parser.add_argument("--all", action="store_true", help="list every commit, not only feat/fix")
    parser.add_argument("--check", metavar="NOTES_JSON")
    args = parser.parse_args()
    if args.check:
        sys.exit(check(args.check))
    if not git("rev-parse", "--is-inside-work-tree"):
        sys.exit("error: run from inside the project's git repository")
    changes(args.since, args.all)


if __name__ == "__main__":
    main()
