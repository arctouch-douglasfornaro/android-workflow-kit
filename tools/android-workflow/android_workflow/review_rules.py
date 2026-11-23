"""Personal review rules: findings that keep coming back, turned into ast-grep checks that never enter the
app's repository.

Rules are ast-grep rule files (`*.yml`) in two local folders:

- `<main checkout>/.ai/review-rules/`: this app's rules. `.ai/` is excluded from git, so they stay yours.
- `~/.ai/review-rules/` (or `$AW_REVIEW_RULES_HOME`): rules for every Android app.

A rule's `severity` is its mode: `error` blocks (the gate fails and the Implementer fixes it), anything
else warns (reported, never a failure). `check` looks only at the lines the change adds, so code that was
already there never trips a rule. `patterns` reads every archived `review.json` of the app (workflow
runs and PR reviews) and groups the findings that share a code term across runs: the candidates for a
new rule. A rule reaches the team only through `promote` in the skill, as a diff the developer opens.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

RULES_DIR = Path(".ai") / "review-rules"
FINDINGS_FILE = "rule-findings.json"
KOTLIN = (".kt", ".kts")
BLOCK_SEVERITIES = {"error"}
TIMEOUT = 120


def _git(target: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(target), *args], text=True, capture_output=True, check=False,
                          stdin=subprocess.DEVNULL)


def main_checkout(target: Path) -> Path:
    found = _git(target, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()
    if not found:
        return target.resolve()
    path = Path(found).resolve()
    return path.parent if path.name == ".git" else path


def home_rules_dir() -> Path:
    return Path(os.environ.get("AW_REVIEW_RULES_HOME") or Path.home() / ".ai" / "review-rules")


def rule_dirs(target: Path) -> list[Path]:
    return [main_checkout(target) / RULES_DIR, home_rules_dir()]


def rule_files(target: Path) -> list[Path]:
    """Every rule file, the app's first; a file of the same name in the app's folder overrides the global one."""
    seen: dict[str, Path] = {}
    for folder in rule_dirs(target):
        if not folder.is_dir():
            continue
        for path in sorted([*folder.glob("*.yml"), *folder.glob("*.yaml")]):
            seen.setdefault(path.name, path)
    return list(seen.values())


def binary() -> str | None:
    return shutil.which("ast-grep")


def mode(severity: str | None) -> str:
    return "block" if (severity or "").lower() in BLOCK_SEVERITIES else "warn"


# --- the lines a change adds ---------------------------------------------------------------------

HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def added_lines(target: Path, base: str | None) -> dict[str, set[int]]:
    """{path: 1-based line numbers the change adds} for Kotlin files, untracked ones whole."""
    diff = _git(target, "diff", base or "HEAD", "--no-ext-diff", "--unified=0", "--no-color", "--", "*.kt", "*.kts")
    added: dict[str, set[int]] = {}
    current: str | None = None
    for line in diff.stdout.splitlines():
        if line.startswith("+++ "):
            name = line[4:]
            current = name[2:] if name.startswith("b/") else None
            continue
        hunk = HUNK.match(line)
        if hunk and current:
            start, count = int(hunk.group(1)), int(hunk.group(2) or 1)
            if count:
                added.setdefault(current, set()).update(range(start, start + count))
    untracked = _git(target, "ls-files", "--others", "--exclude-standard", "--", "*.kt", "*.kts")
    for name in untracked.stdout.splitlines():
        path = target / name
        if name.strip() and path.is_file():
            count = len(path.read_text(encoding="utf-8", errors="replace").splitlines())
            added[name] = set(range(1, count + 1))
    return added


# --- running the rules ---------------------------------------------------------------------------

def scan(target: Path, rule: Path, paths: list[str]) -> tuple[list[dict[str, Any]], str | None]:
    """ast-grep matches of one rule over `paths` (relative to target); (matches, error)."""
    tool = binary()
    if not tool:
        return [], "ast-grep is not installed"
    try:
        done = subprocess.run([tool, "scan", "--rule", str(rule), "--json=compact", *paths], cwd=target,
                              text=True, capture_output=True, check=False, timeout=TIMEOUT, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return [], f"{rule.name}: ast-grep took more than {TIMEOUT}s"
    try:
        matches = json.loads(done.stdout or "[]") if done.returncode in (0, 1) else None  # 1: an error-severity match
    except json.JSONDecodeError:
        matches = None
    if not isinstance(matches, list):
        lines = [line.strip() for line in (done.stderr or done.stdout).splitlines() if line.strip()]
        reason = next((line for line in lines if "error:" in line), lines[0] if lines else "ast-grep failed")
        return [], f"{rule.name}: {reason.lstrip('╰▻ ')[:300]}"
    return [item for item in matches if isinstance(item, dict)], None


def _finding(match: dict[str, Any], rule: Path) -> dict[str, Any]:
    start = ((match.get("range") or {}).get("start") or {})
    severity = match.get("severity") or "warning"
    return {
        "rule": match.get("ruleId") or rule.stem,
        "mode": mode(severity),
        "file": match.get("file"),
        "line": int(start.get("line", 0)) + 1,
        "message": (match.get("message") or "").strip(),
        "code": (match.get("lines") or match.get("text") or "").strip()[:200],
        "rule_file": str(rule),
    }


def check(target: Path, base: str | None) -> dict[str, Any]:
    """Run every local rule over the lines the change adds.

    `status`: `passed` / `failed` (a `block` rule matched) / `skipped` (no rule, no ast-grep or no Kotlin
    change). A broken rule is an `errors` entry, never a failure of the change.
    """
    rules = rule_files(target)
    if not rules:
        return {"status": "skipped", "reason": "no local review rules", "rules": 0, "findings": []}
    if not binary():
        return {"status": "skipped", "reason": "ast-grep is not installed (brew install ast-grep)",
                "rules": len(rules), "findings": []}
    added = added_lines(target, base)
    if not added:
        return {"status": "skipped", "reason": "no Kotlin line changed", "rules": len(rules), "findings": []}
    paths = sorted(path for path in added if (target / path).is_file())
    findings: list[dict[str, Any]] = []
    errors: list[str] = []
    for rule in rules:
        matches, error = scan(target, rule, paths)
        if error:
            errors.append(error)
            continue
        for match in matches:
            item = _finding(match, rule)
            end = int((((match.get("range") or {}).get("end") or {}).get("line", item["line"] - 1))) + 1
            if added.get(item["file"] or "", set()) & set(range(item["line"], end + 1)):
                findings.append(item)
    findings.sort(key=lambda item: (item["mode"] != "block", item["file"] or "", item["line"]))
    blocked = any(item["mode"] == "block" for item in findings)
    result: dict[str, Any] = {"status": "failed" if blocked else "passed", "rules": len(rules),
                              "files": len(paths), "findings": findings}
    if errors:
        result["errors"] = errors
    return result


def check_safely(target: Path, base: str | None) -> dict[str, Any]:
    """`check` for the gate: a problem with the rules themselves never stops the change."""
    try:
        return check(target, base)
    except Exception as error:  # noqa: BLE001 - reported in the gate, the change goes on
        return {"status": "skipped", "reason": f"review rules failed: {str(error)[:200]}", "findings": []}


def test(target: Path, rule: Path) -> dict[str, Any]:
    """How often a rule matches the whole app (tracked Kotlin files): a rule that matches hundreds of
    lines warns on every touch of old code nearby, one that matches nothing may be wrong."""
    files = [name for name in _git(target, "ls-files", "--", "*.kt", "*.kts").stdout.splitlines() if name.strip()]
    if not files:
        return {"rule": rule.stem, "matches": 0, "files": 0, "examples": []}
    matches: list[dict[str, Any]] = []
    for start in range(0, len(files), 2000):
        found, error = scan(target, rule, files[start:start + 2000])
        if error:
            return {"rule": rule.stem, "error": error}
        matches.extend(found)
    items = [_finding(match, rule) for match in matches]
    return {
        "rule": items[0]["rule"] if items else rule.stem,
        "mode": items[0]["mode"] if items else None,
        "matches": len(items),
        "files": len({item["file"] for item in items}),
        "examples": [f"{item['file']}:{item['line']}" for item in items[:5]],
    }


def listing(target: Path) -> list[dict[str, Any]]:
    rows = []
    app = main_checkout(target) / RULES_DIR
    for path in rule_files(target):
        text = path.read_text(encoding="utf-8", errors="replace")
        rule_id = re.search(r"(?m)^id:\s*(\S+)", text)
        severity = re.search(r"(?m)^severity:\s*(\S+)", text)
        rows.append({"rule": rule_id.group(1) if rule_id else path.stem,
                     "mode": mode(severity.group(1) if severity else None),
                     "scope": "app" if path.parent == app else "global", "file": str(path)})
    return rows


# --- patterns across reviews ---------------------------------------------------------------------

TERM = re.compile(r"`([^`\n]{3,60})`|\b([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\b")
CAMEL = re.compile(r"[a-z][A-Z]|[A-Z]{2,}_[A-Z]")


def _code_term(word: str) -> bool:
    return len(word) >= 5 and bool(CAMEL.search(word)) and not re.fullmatch(r"(?:AC|PR|C|S|B)\d+", word)


def terms(text: str, file: str | None) -> set[str]:
    """The code terms a finding names (backticked or camelCase) and the name of its file: a file that
    collects findings run after run is a pattern too."""
    found: set[str] = set()
    for quoted, word in TERM.findall(text):
        if quoted:
            found.add(quoted.strip())
        elif _code_term(word):
            found.add(word.split(".")[-1] if "." in word and not word[0].isupper() else word)
    name = Path(str(file or "").split(":", 1)[0].strip()).name
    if name and " " not in name:
        found.add(name)
    return {term for term in found if term}


def _checkouts(target: Path) -> list[Path]:
    main = main_checkout(target)
    listed = _git(main, "worktree", "list", "--porcelain").stdout.splitlines()
    roots = [Path(line[len("worktree "):]) for line in listed if line.startswith("worktree ")]
    return [root for root in dict.fromkeys([main, *roots]) if root.is_dir()]


def review_files(target: Path) -> list[tuple[str, Path]]:
    """(run id, review.json) of every run of the app: main checkout, its worktrees and the archive."""
    found: dict[str, Path] = {}
    for root in _checkouts(target):
        workflow = root / ".ai" / "workflow"
        for path in [*workflow.glob("*/review.json"), *workflow.glob("_archive/*/review.json")]:
            found.setdefault(path.parent.name, path)
    return sorted(found.items())


def findings_of(run: str, path: Path) -> list[dict[str, Any]]:
    try:
        review = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = []
    for kind in ("blocking", "concerns", "suggestions"):
        for index, item in enumerate(review.get(kind) or [], 1):
            if isinstance(item, str):
                item = {"problem": item}
            if not isinstance(item, dict):
                continue
            text = str(item.get("problem") or item.get("title") or item.get("body") or "").strip()
            if not text:
                continue
            file = item.get("file")
            if file and item.get("line") and ":" not in str(file):
                file = f"{file}:{item['line']}"
            rows.append({"run": run, "kind": kind, "id": item.get("id") or f"{kind[0].upper()}{index}",
                         "file": file, "text": text})
    return rows


def patterns(target: Path, minimum: int = 2) -> dict[str, Any]:
    """Code terms that findings of `minimum` or more runs share, most runs first, with the findings and the
    local rule that already names the term (if any)."""
    reviews = review_files(target)
    rows = [row for run, path in reviews for row in findings_of(run, path)]
    by_term: dict[str, list[int]] = {}
    for index, row in enumerate(rows):
        for term in terms(row["text"], row["file"]):
            by_term.setdefault(term, []).append(index)
    groups: dict[tuple[int, ...], list[str]] = {}
    for term, indexes in by_term.items():
        if len({rows[i]["run"] for i in indexes}) >= minimum:
            groups.setdefault(tuple(sorted(set(indexes))), []).append(term)
    rules_text = {path.stem: path.read_text(encoding="utf-8", errors="replace") for path in rule_files(target)}
    out = []
    for indexes, names in groups.items():
        names.sort(key=lambda term: (-len(term), term))
        covered = sorted(rule for rule, text in rules_text.items() if any(term in text for term in names))
        runs = sorted({rows[i]["run"] for i in indexes})
        out.append({
            "terms": names,
            "runs": runs,
            "count": len(runs),
            "findings": [rows[i] for i in indexes],
            **({"covered_by": covered} if covered else {}),
        })
    out.sort(key=lambda group: (-group["count"], -len(group["findings"]), group["terms"][0]))
    return {"reviews": len(reviews), "findings": len(rows), "minimum": minimum, "patterns": out,
            "rules_dir": str(main_checkout(target) / RULES_DIR), "global_rules_dir": str(home_rules_dir())}
