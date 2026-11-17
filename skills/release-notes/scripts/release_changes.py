#!/usr/bin/env python3
"""Release notes helper for the release-notes skill. Standard library only, read-only.

Default: print the project version, the store locales (detected), the baseline commit and the
feat/fix commits since it.
  python3 release_changes.py [--since <ref|YYYY-MM-DD>] [--all]
--locales: print only the detected locales and where they came from.
--check <notes.json>: check What's New (<= 500, Google Play) and Promotional Text (<= 170, App Store)
for every locale in {"en-US": {"whatsNew": "...", "promotionalText": "..."}, ...}, and that every
detected locale is there.
--play <notes.json>: print the Google Play Console release notes block (<en-US>...</en-US>, sorted).
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
    for pbx in walk("**/*.xcodeproj/project.pbxproj"):
        text = pbx.read_text(errors="ignore")
        name = re.search(r"MARKETING_VERSION = ([^;]+);", text)
        code = re.search(r"CURRENT_PROJECT_VERSION = ([^;]+);", text)
        if name:
            found.append(f"iOS {name.group(1)} ({code.group(1) if code else '?'}) in {pbx}")
    return found


# Default store region per bare language code (Google Play / App Store Connect locale tags).
DEFAULT_REGION = {
    "en": "en-US", "pt": "pt-BR", "es": "es-ES", "fr": "fr-FR", "de": "de-DE", "it": "it-IT",
    "ja": "ja-JP", "ko": "ko-KR", "zh": "zh-CN", "ru": "ru-RU", "nl": "nl-NL", "pl": "pl-PL",
    "tr": "tr-TR", "sv": "sv-SE", "da": "da-DK", "fi": "fi-FI", "nb": "no-NO", "no": "no-NO",
    "cs": "cs-CZ", "el": "el-GR", "he": "iw-IL", "iw": "iw-IL", "hu": "hu-HU", "id": "id",
    "ms": "ms", "th": "th", "uk": "uk", "vi": "vi", "ar": "ar", "hi": "hi-IN", "ro": "ro",
}
LOCALE_RE = re.compile(r"^[a-z]{2,3}(-[A-Z]{2}|-[0-9]{3}|-Han[st])?$")
SKIP_DIRS = {"build", "Pods", "SourcePackages", "DerivedData", "node_modules", ".gradle", ".git", "archive"}


def walk(pattern):
    for path in Path(".").glob(pattern):
        if not any(part in SKIP_DIRS or (part.startswith(".") and part != ".") for part in path.parts[:-1]):
            yield path


def to_tag(code):
    code = code.replace("_", "-").replace("-r", "-")
    if code.lower() in ("base", "default"):
        return None
    lang, _, region = code.partition("-")
    if region:
        return f"{lang.lower()}-{region.upper() if len(region) == 2 else region}"
    return DEFAULT_REGION.get(lang.lower(), lang.lower())


def store_locales():
    """Locales the stores already have texts for, from the project's store metadata."""
    meta = Path("STORE_METADATA.json")
    if meta.exists():
        data = json.loads(meta.read_text(encoding="utf-8"))
        block = data.get("localizations") if isinstance(data.get("localizations"), dict) else data
        found = sorted(k for k in block if LOCALE_RE.match(k))
        if found:
            return found, str(meta)
    for base in ("fastlane/metadata/android", "fastlane/metadata"):
        root = Path(base)
        if root.is_dir():
            found = sorted(p.name for p in root.iterdir() if p.is_dir() and LOCALE_RE.match(p.name))
            if found:
                return found, base
    return [], None


def app_languages():
    """UI languages the app ships, mapped to store tags (default region when the code has none)."""
    found, sources = set(), set()
    for res in walk("**/res/values-*/strings.xml"):
        tag = to_tag(res.parent.name[len("values-"):])
        if tag and re.match(r"^[a-z]{2,3}(-|$)", tag):
            found.add(tag); sources.add("Android res/values-*")
    for res in walk("**/composeResources/values-*/strings.xml"):
        tag = to_tag(res.parent.name[len("values-"):])
        if tag:
            found.add(tag); sources.add("composeResources/values-*")
    for lproj in walk("**/*.lproj"):
        tag = to_tag(lproj.name[: -len(".lproj")])
        if tag:
            found.add(tag); sources.add("iOS *.lproj")
    # Hand-rolled language enums, e.g. ENGLISH("en", ...) in Kotlin or case en = "en" in Swift.
    enum_re = re.compile(r'\b[A-Z][A-Z_]+\(\s*"([a-z]{2}(?:[-_][A-Z]{2})?)"')
    for src in list(walk("**/*.kt")) + list(walk("**/*.swift")):
        text = src.read_text(errors="ignore")
        if "enum class Language" not in text and "enum Language" not in text:
            continue
        for code in enum_re.findall(text):
            found.add(to_tag(code)); sources.add(str(src))
    return sorted(found), sorted(sources)


def locales(verbose=True):
    store, store_src = store_locales()
    app, app_src = app_languages()
    chosen = store or app or ["en-US"]
    if verbose:
        if store:
            print(f"Store locales: {', '.join(store)}  (from {store_src})")
        if app:
            print(f"App languages: {', '.join(app)}  (from {'; '.join(app_src)})")
        if store and app and set(app) - set(store):
            print(f"  note: the app has {', '.join(sorted(set(app) - set(store)))} but the store texts don't; ask whether to add them")
        if not store and not app:
            print("Locales: none found, defaulting to en-US; ask the user")
        print(f"Write: {', '.join(chosen)}")
    return chosen


def play_block(path):
    notes = json.loads(Path(path).read_text(encoding="utf-8"))
    print("\n".join(f"<{loc}>\n{notes[loc]['whatsNew'].strip()}\n</{loc}>" for loc in sorted(notes)))


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
    locales()
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
    missing = [loc for loc in locales(verbose=False) if loc not in notes]
    if missing:
        print(f"MISSING locales: {', '.join(missing)}")
        ok = False
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
    parser.add_argument("--play", metavar="NOTES_JSON", help="print the Google Play release notes block")
    parser.add_argument("--locales", action="store_true", help="print only the detected locales")
    args = parser.parse_args()
    if args.check:
        sys.exit(check(args.check))
    if args.play:
        play_block(args.play)
        return
    if args.locales:
        locales()
        return
    if not git("rev-parse", "--is-inside-work-tree"):
        sys.exit("error: run from inside the project's git repository")
    changes(args.since, args.all)


if __name__ == "__main__":
    main()
