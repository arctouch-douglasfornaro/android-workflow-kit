"""Pick the Android skills each agent reads for this ticket, so nobody has to ask for them.

Candidates are the kit's skills (`skills/android/`, topics in `catalog.json`) and the app's own
skills (`TARGET/.ai/skills`, `.claude/skills`, `.agents/skills`, `.cursor/skills`). Signals come from
the ticket (text, surfaces), the likely files and the files already changed (their content), and a
failing gate. The choice is deterministic and costs no tokens; it is written to `RUN/skills.json`
with the reason for each skill. A project skill on the same topic wins over the kit's, and the
Planner can add or drop skills (`skills --add/--drop`), which later refreshes keep.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from android_workflow.paths import KIT_ROOT, run_dir

SKILLS_NAME = "skills.json"
KIT_SKILLS = KIT_ROOT / "skills" / "android"
PROJECT_DIRS = (".ai/skills", ".claude/skills", ".agents/skills", ".cursor/skills")
DEFAULT_MAX = 4
MAX_FILE_BYTES = 200_000
MAX_FILES = 25

# The topic a project skill covers, from its name (a description mentions too much to be a signal:
# a design-system builder "uses Jetpack Compose" without being the Compose rules).
NAME_TOPICS = (
    (r"compose.*performance|performance.*compose|recompos", ["compose-performance"]),
    (r"\bcompose\b", ["compose"]),
    (r"performance|jank|startup", ["performance"]),
    (r"navigation|deeplink|deep-link", ["navigation"]),
    (r"insets|edge-to-edge", ["insets"]),
    (r"\btests?\b|testing", ["testing"]),
    (r"strings?|locali[sz]ation|translation", ["strings"]),
    (r"ktlint|\blint\b|format", ["lint-fix"]),
    (r"screenshots?|previews?", ["screenshots"]),
)
# Skills about running the team's process, not about writing code.
NOT_FOR_CODE = re.compile(r"triage|jira|ticket|dependabot|skill-(?:create|update)|investigation|release|vitals", re.I)
STOP_WORDS = {
    "android", "skill", "skills", "builder", "component", "components", "rules", "guide", "migration", "content",
    "list", "item", "items", "data", "layer", "client", "action", "processing", "catalog", "tool", "tools", "improvements",
    "testing", "unit", "strings", "compose", "performance", "create", "update", "using", "adding",
    "feature", "features", "overview", "docs", "screen", "screens", "view", "views", "model", "models", "main", "test",
}
PERF_WORDS = r"\bjank|\bscroll|\blag\b|\bslow|stutter|recompos|performance|\bfps\b|smooth|frame drop"


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _frontmatter(text: str) -> dict[str, str]:
    match = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not match:
        return {}
    meta: dict[str, str] = {}
    key = None
    for line in match.group(1).splitlines():
        found = re.match(r"^([A-Za-z_-]+):\s*(.*)$", line)
        if found:
            key, value = found.group(1), found.group(2).strip()
            meta[key] = value.strip('"').strip("'") if value not in {">-", ">", "|", "|-"} else ""
        elif key and line.startswith(" "):
            meta[key] = (meta[key] + " " + line.strip()).strip()
    return meta


def _first_paragraph(text: str) -> str:
    body = re.sub(r"^---\n.*?\n---\n", "", text, flags=re.S)
    for block in re.split(r"\n\s*\n", body):
        block = block.strip()
        if block and not block.startswith(("#", ">", "---", "```", "|")):
            return re.sub(r"\s+", " ", block)[:400]
    return ""


def project_skills(target: Path) -> list[dict[str, Any]]:
    """The app's own skills, one per name. The file with the real content is the one to read; the
    description comes from whichever copy has frontmatter (tools keep thin pointers next to it)."""
    found: dict[str, dict[str, Any]] = {}
    for folder in PROJECT_DIRS:
        root = target / folder
        if not root.is_dir():
            continue
        for path in sorted(root.glob("*/SKILL.md")):
            name = path.parent.name
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            meta = _frontmatter(text)
            pointer = len(text) < 1200 and "skills/" in text and ("lives in" in text or "Canonical" in text or "Pointer" in text)
            item = found.setdefault(name, {"name": name, "source": "project", "path": None, "description": "",
                                           "user_only": False, "fallback": ""})
            if meta.get("description") and not item["description"]:
                item["description"] = meta["description"]  # a tool's frontmatter is the best summary
            if str(meta.get("disable-model-invocation", "")).lower() == "true":
                item["user_only"] = True
            if item["path"] is None and not pointer:
                item["path"] = path.relative_to(target).as_posix()
                item["fallback"] = _first_paragraph(text)
    for item in found.values():
        if item["path"] is None:  # only pointers were found: read the first one
            candidates = [target / folder / item["name"] / "SKILL.md" for folder in PROJECT_DIRS]
            existing = [path for path in candidates if path.is_file()]
            if existing:
                item["path"] = existing[0].relative_to(target).as_posix()
        item["description"] = item["description"] or item.pop("fallback", "")
        item.pop("fallback", None)
    return sorted((item for item in found.values() if item["path"]), key=lambda item: item["name"])


def kit_skills() -> list[dict[str, Any]]:
    catalog = (_json(KIT_SKILLS / "catalog.json").get("skills")) or {}
    skills = []
    for name, entry in sorted(catalog.items()):
        path = KIT_SKILLS / name / "SKILL.md"
        if not path.is_file():
            continue
        meta = _frontmatter(path.read_text(encoding="utf-8"))
        skills.append({
            "name": name, "source": "kit", "path": str(path), "description": meta.get("description", ""),
            "topics": list(entry.get("topics") or []), "roles": list(entry.get("roles") or ["implementer"]),
        })
    return skills


def _topics_of(name: str) -> set[str]:
    words = name.lower().replace("_", "-").replace("-", " ")
    for pattern, topics in NAME_TOPICS:
        if re.search(pattern, words):
            return set(topics)
    return set()


def _read(path: Path) -> str:
    try:
        with path.open("rb") as handle:
            return handle.read(MAX_FILE_BYTES).decode("utf-8", errors="replace")
    except OSError:
        return ""


def signals(target: Path) -> dict[str, Any]:
    """What this ticket is about: topics with the evidence for each, and words for project skills."""
    from android_workflow.live import live_changes

    run = run_dir(target)
    spec = _json(run / "ticket-spec.json")
    ticket = spec.get("ticket") or {}
    text = " ".join(str(part) for part in [
        ticket.get("title"), ticket.get("description"), spec.get("reproduction"),
        *(spec.get("acceptance_criteria") or []),
    ] if part)
    surfaces = set(spec.get("surfaces") or [])
    change_set = _json(run / "change-set-map.json")
    paths: list[str] = [str(item.get("path") if isinstance(item, dict) else item) for item in change_set.get("candidate_files") or []][:10]
    changed = [item["path"] for item in live_changes(target) if item.get("status") != "D"]
    paths = list(dict.fromkeys(changed + paths))[:MAX_FILES]
    code = "\n".join(_read(target / path) for path in paths if (target / path).is_file())
    found: dict[str, list[str]] = {}

    def add(topic: str, why: str) -> None:
        found.setdefault(topic, [])
        if why not in found[topic]:
            found[topic].append(why)

    compose_code = "@Composable" in code or "androidx.compose" in code
    if compose_code:
        add("compose", "the code it touches is Jetpack Compose")
    if re.search(r"\bcompose\b|composable", text, re.I):
        add("compose", "the ticket mentions Compose")
    perf_text = re.search(PERF_WORDS, text, re.I)
    if perf_text:
        add("performance", f"the ticket mentions {perf_text.group(0).strip().lower()}")
    if "compose" in found and (perf_text or re.search(r"\bLazy(?:Column|Row|VerticalGrid|HorizontalGrid)\b|animate\w*As", code)):
        add("compose-performance", "Compose lists or animation" if not perf_text else "Compose and a performance concern")
    if surfaces & {"navigation", "deeplink"}:
        add("navigation", "the ticket's surfaces include navigation")
    if re.search(r"\bNavHost\b|navController|rememberNavController|nav_graph|NavGraphBuilder", code):
        add("navigation", "the code it touches defines navigation")
    if re.search(r"navigat|deep ?link|back ?stack", text, re.I):
        add("navigation", "the ticket mentions navigation")
    if re.search(r"enableEdgeToEdge|WindowInsets|imePadding|safeDrawing|systemBarsPadding", code):
        add("insets", "the code it touches handles window insets")
    if re.search(r"edge-to-edge|\binsets?\b|status bar|navigation bar|keyboard covers|\bime\b", text, re.I):
        add("insets", "the ticket mentions insets or system bars")
    if any(path.endswith("strings.xml") for path in paths) or re.search(r"\bstrings?\b|\bcopy\b|translat|wording|\blabel", text, re.I):
        add("strings", "user-facing strings")
    add("testing", "the Implementer writes the tests that prove the change")
    if "ui" in surfaces or compose_code:
        add("screenshots", "a visible change gets a preview")
    gate = _json(run / "gate-report.json")
    for step in gate.get("steps") or []:
        if step.get("outcome") == "failed" and re.search(r"ktlint|lintKotlin|formatKotlin|spotless|detekt", str(step.get("command") or ""), re.I):
            add("lint-fix", "the quality gate reports format or lint findings")
            break
    if "T7" in (spec.get("route") or []):
        add("device", "the run has a Device stage")
        if shutil.which("android"):
            add("android-cli", "the Android CLI is installed")
    words = set(re.findall(r"[a-z][a-z0-9]{3,}", " ".join([text, " ".join(paths)]).lower()))
    words |= set(re.findall(r"[a-z][a-z0-9]{3,}", re.sub(r"([a-z])([A-Z])", r"\1 \2", code).lower())) if code else set()
    return {"topics": found, "words": words}


def _choose(candidates: list[dict[str, Any]], context: dict[str, Any], role: str, limit: int,
            forced: list[str], dropped: list[str]) -> list[dict[str, Any]]:
    topics: dict[str, list[str]] = context["topics"]
    scored = []
    for skill in candidates:
        if skill["name"] in dropped:
            continue
        if skill["source"] == "kit" and role not in skill["roles"] and skill["name"] not in forced:
            continue
        if skill["source"] == "project" and role == "device":
            continue  # a project's skills are about its code
        why: list[str] = []
        score = 0
        for topic in skill["topics"]:
            if topic in topics:
                score += 3
                why.extend(topics[topic][:1])
        if skill["source"] == "project":
            names = [word for word in skill["name"].lower().split("-") if len(word) >= 4 and word not in STOP_WORDS]
            hits = [word for word in names if word in context["words"]]
            if hits:
                score += 2 * len(hits)
                why.append(f"the ticket or code mentions {', '.join(hits)}")
        if score > 0 and skill["source"] == "project":
            score += 1  # the app's own knowledge first, at equal relevance
        if skill["name"] in forced:
            score += 100
            why.insert(0, "added by the Planner")
        if score > 0:
            scored.append((score, skill["source"] == "project", skill, why))
    scored.sort(key=lambda item: (-item[0], not item[1], item[2]["name"]))
    chosen: list[dict[str, Any]] = []
    covered: set[str] = set()
    for score, _project, skill, why in scored:
        if len(chosen) >= limit and skill["name"] not in forced:
            continue
        if skill["source"] == "kit" and skill["topics"] and set(skill["topics"]) <= covered and skill["name"] not in forced:
            continue  # the app's own skill already covers this topic
        chosen.append({"name": skill["name"], "source": skill["source"], "path": skill["path"],
                       "topics": skill["topics"], "why": why, "description": skill["description"][:300]})
        if skill["source"] == "project":
            covered |= set(skill["topics"]) & set(context["topics"])
    # A project skill that ranked below a kit skill on the same topic still wins it.
    return [item for item in chosen if not (item["source"] == "kit" and item["topics"] and set(item["topics"]) <= covered
                                            and item["name"] not in forced)]


def max_skills(target: Path) -> int:
    for path in (target / ".ai" / "android-workflow.json",):
        limit = (_json(path).get("skills") or {}).get("max")
        if isinstance(limit, int) and 1 <= limit <= 8:
            return limit
    return DEFAULT_MAX


def select(target: Path, add: list[str] | None = None, drop: list[str] | None = None) -> dict[str, Any]:
    """Write `RUN/skills.json`: the skills each agent reads, with why. Manual choices are kept."""
    from android_workflow.cli import write_json

    path = run_dir(target) / SKILLS_NAME
    previous = _json(path).get("manual") or {}
    forced = list(dict.fromkeys([*(previous.get("add") or []), *(add or [])]))
    dropped = list(dict.fromkeys([*(previous.get("drop") or []), *(drop or [])]))
    forced = [name for name in forced if name not in (drop or [])]
    dropped = [name for name in dropped if name not in (add or [])]
    project = [skill for skill in project_skills(target)
               if not skill["user_only"] and not NOT_FOR_CODE.search(skill["name"])]
    for skill in project:
        skill["topics"] = sorted(_topics_of(skill["name"]))
    candidates = project + kit_skills()
    known = {skill["name"] for skill in candidates}
    unknown = [name for name in forced if name not in known]
    if unknown:
        raise ValueError(f"unknown skill(s): {', '.join(unknown)}; known: {', '.join(sorted(known))}")
    context = signals(target)
    result = {
        "schema_version": 1,
        "implementer": _choose(candidates, context, "implementer", max_skills(target), forced, dropped),
        "device": _choose(candidates, context, "device", 2, [name for name in forced if any(
            s["name"] == name and s["source"] == "kit" and "device" in s["roles"] for s in candidates)], dropped)
        if "device" in context["topics"] else [],
        "signals": {topic: why for topic, why in sorted(context["topics"].items())},
        "considered": sorted(known),
        "manual": {"add": forced, "drop": dropped},
    }
    write_json(path, result)
    return result


def refresh(target: Path) -> None:
    """Recompute after the ticket or the code moved; never fails the command that called it."""
    try:
        if (run_dir(target) / "ticket-spec.json").is_file():
            select(target)
    except Exception:  # noqa: BLE001 - a skill hint is never a gate
        pass
