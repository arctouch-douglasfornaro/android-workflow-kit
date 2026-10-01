#!/usr/bin/env python3
"""Verify a host's agent registry agrees with the canonical role files.

Phase 0.4b hard-stops a run when a role the track will spawn is missing, or when its registered
description contradicts its role file — a mis-described role spawns successfully and then the WRONG
agent runs. This makes that check cheap enough to run before a ticket instead of discovering it
mid-flight.

    python3 ~/.ai/bin/check_registry.py --registry ~/.claude/agents
    python3 ~/.ai/bin/check_registry.py --registry ~/.cursor/agents --roles ~/.ai/agents

**Pass the registry of the host actually running the workflow.** The default is Claude Code's, so
checking it from another tool validates a registry that host never reads — and a Cursor run would
pass 0.4b and then die at the first `implementer` spawn, exactly the failure 0.4b exists to catch.
Phase 0.4 already resolves a tool-specific skills dir; use the sibling `agents` dir here.

Exit 0 = every canonical role is registered and consistent. Exit 1 = Phase 0.4b's table applies.
Agents in the registry that the kit does not own are listed as `extra` and never fail the check.
"""
from __future__ import annotations

import argparse
import os
import re
import sys

# Canonical phase per role, from workflows/feature-workflow.md § Phases. The value is the phase the
# role IS; a role file may legitimately mention other phases (routing, hand-offs), so only the
# role's own phase claim is checked.
CANONICAL_PHASE = {
    "workspace-agent": "0",
    "project-explorer": None,   # runs from /workflow-setup, not a numbered feature phase
    "designer": "1",
    "implementer": "2",
    "test-writer": "3",
    "feature-reviewer": "5",
    "device-pass": "7",
    "ship-agent": "9",
    "pr-author": "9",
}

# Retired roles, matched as whole spawnable names so prose like "no separate architect" is fine.
RETIRED = ("product-planner", "feature-architect", "commit-agent", "push-agent",
           "manual-tester", "visual-evidence", "feature-navigator")

PHASE_RE = re.compile(r"[Pp]hase\s+(\d+)")
# The lookahead must include the frontmatter's closing `---`. Without it, a `description:` that is
# the LAST frontmatter key runs to end-of-file and swallows the whole body -- which is 4 of the 9
# role files -- so a body cross-reference like "route back to Phase 6" reads as the role's own phase
# claim and hard-stops a healthy run. That is the exact false positive this scoping exists to avoid.
# `[a-z_-]` includes the hyphen: Claude Code frontmatter permits keys like `allowed-tools:`, and a
# key the lookahead does not recognize gets swallowed into the description along with any phase
# number in it.
DESCRIPTION_RE = re.compile(r"^description:\s*(.*?)(?=^[a-z][a-z_-]*:\s|^---\s*$|\Z)", re.S | re.M)


STOPWORDS = frozenset("""
a an and are as at be been but by for from has have in into is it its of on or that the this to
use used uses using with when where which who will not never only also always any each per
agent phase feature workflow code the writes write runs run
""".split())


def headline_of(text: str) -> str:
    """A role file's own summary: its first non-heading, non-empty prose paragraph."""
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", ">", "-", "|", "`", "---")):
            if lines:
                break
            continue
        lines.append(stripped)
        if len(lines) >= 3:
            break
    return " ".join(lines)


def significant_words(text: str) -> set[str]:
    """Lowercased words of 4+ characters, minus filler — enough to tell two role summaries apart."""
    words = re.findall(r"[A-Za-z][A-Za-z-]{3,}", (text or "").lower())
    return {w for w in words if w not in STOPWORDS}


def md_files(directory: str) -> set[str]:
    try:
        return {os.path.splitext(f)[0] for f in os.listdir(directory)
                if f.endswith(".md") and not f.startswith("_")}
    except FileNotFoundError:
        return set()


def description_of(text: str) -> str:
    """The frontmatter description — the only part the host shows when routing a spawn."""
    m = DESCRIPTION_RE.search(text)
    return m.group(1) if m else ""


def own_phase(text: str) -> str | None:
    """The phase the role claims as its own, read from the DESCRIPTION only.

    Scanning the whole file would flag a legitimate cross-reference ("on NEEDS_FIXES route back to
    Phase 6") as a contradiction and hard-stop a healthy run. The description is where a role states
    its slot, and it is what the host reads when choosing an agent.
    """
    phases = sorted(set(PHASE_RE.findall(description_of(text))), key=int)
    return phases[0] if len(phases) == 1 else (",".join(phases) if phases else None)


def check(registry: str, roles: str) -> int:
    problems = 0

    if not os.path.isdir(registry):
        print(f"FAIL registry directory does not exist: {registry}")
        print("     Every canonical role is therefore unspawnable on this host.")
        return 1
    if not os.path.isdir(roles):
        print(f"FAIL roles directory does not exist: {roles}")
        return 1

    reg, rol = md_files(registry), md_files(roles)

    for missing in sorted(rol - reg):
        print(f"FAIL {missing:<20} role file exists but this host registers nothing — unspawnable")
        problems += 1

    # Extras are somebody's own agents. They are not the kit's business and never a hard stop.
    extra = sorted(reg - rol)
    if extra:
        print(f"     extra (not kit roles, ignored): {', '.join(extra)}")

    # Each role file's own first-line summary, used to detect a wrapper carrying another role's
    # description -- the `feature-reviewer`-described-as-`feature-overview-doc` incident.
    role_headlines = {}
    for r in sorted(rol):
        body = open(os.path.join(roles, f"{r}.md"), encoding="utf-8").read()
        role_headlines[r] = headline_of(body)

    for role in sorted(reg & rol):
        text = open(os.path.join(registry, f"{role}.md"), encoding="utf-8").read()
        notes = []

        # Does this wrapper's description describe a DIFFERENT role better than its own? Compared on
        # distinctive words, so paraphrase is fine and a wholesale copy-paste is not.
        desc_words = significant_words(description_of(text))
        if desc_words:
            scores = {r: len(desc_words & significant_words(h)) for r, h in role_headlines.items()}
            best = max(scores, key=lambda r: (scores[r], r == role))
            if best != role and scores[best] > scores.get(role, 0):
                notes.append(
                    f"description matches role '{best}' better than its own "
                    f"({scores[best]} vs {scores.get(role, 0)} distinctive words)"
                )

        if not re.search(r"^name:\s*" + re.escape(role) + r"\s*$", text, re.M):
            notes.append("frontmatter name does not match the filename")

        stated, want = own_phase(text), CANONICAL_PHASE.get(role, "?")
        if want is None:
            if stated:
                notes.append(f"claims Phase {stated} but owns no numbered phase")
        elif stated is None:
            notes.append(f"states no phase of its own (canonical: {want})")
        elif stated != want:
            notes.append(f"claims Phase {stated}, canonical is {want}")

        desc = description_of(text)
        for retired in RETIRED:
            if re.search(r"(?<![\w-])" + re.escape(retired) + r"(?![\w-])", desc):
                notes.append(f"description names retired role '{retired}'")

        if notes:
            print(f"FAIL {role:<20} " + "; ".join(notes))
            problems += 1
        else:
            print(f"ok   {role:<20} phase {stated or 'n/a'}")

    print()
    if problems:
        print(f"{problems} problem(s) in {registry} — Phase 0.4b would hard-stop. "
              "Fix before running a ticket.")
    else:
        print(f"consistent: {len(reg & rol)} kit roles registered in {registry}")
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--registry", default=os.path.expanduser("~/.claude/agents"),
                    help="The HOST's agent registry (default: Claude Code's)")
    ap.add_argument("--roles", default=os.path.expanduser("~/.ai/agents"),
                    help="Canonical role files (default: ~/.ai/agents)")
    args = ap.parse_args(argv)
    return check(os.path.expanduser(args.registry), os.path.expanduser(args.roles))


if __name__ == "__main__":
    sys.exit(main())
