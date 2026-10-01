#!/usr/bin/env python3
"""Enumerate skill/feature-README/PR-template metadata for feature-workflow Phase 0.

Self-contained, token-cheap helper for the orchestrator's Phase 0 (capability +
feature-context discovery). Replaces opening every `.ai/skills/*/SKILL.md` and
every `features/**/README.md` in full just to read a few frontmatter fields —
this does the enumeration/parsing deterministically and prints the tables the
orchestrator pastes into `00-capabilities.md` / `00-feature-context.md`.

Matching candidates against ticket signals is still a judgment call for the
orchestrator/agents (see feature-workflow.md § Phase 0 — Feature context
discovery, rule 2 onward) — this script only does the cheap, deterministic
part: enumerate + parse frontmatter.

Frontmatter parsing handles inline values, folded/literal block scalars (`>-`, `|`) and YAML
lists, because the kit's own skills use `description: >-` and every feature README writes `covers`
as a block list. Reading only inline values gave the capability table the literal `>-` as each
folded skill's description, and reduced a 4-module `covers` list to the parent directory.

Tests: `python3 feature_discovery_test.py`.
"""
import argparse
import glob
import os

FRONTMATTER_DELIM = "---"
FRONTMATTER_READ_BYTES = 4000  # frontmatter is always near the top of the file


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested)
# ---------------------------------------------------------------------------

def parse_frontmatter(text):
    """Returns dict of top-level `key: value` frontmatter fields, or {} if none.

    `text` is the (partial, e.g. first FRONTMATTER_READ_BYTES) file content.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != FRONTMATTER_DELIM:
        return {}
    # No closing delimiter in what we read means the read was truncated (or the file is malformed).
    # Parsing on would continue into the markdown body, where any unindented `key: value` line --
    # a body line starting `description:` for instance -- would overwrite the real field.
    if FRONTMATTER_DELIM not in [l.strip() for l in lines[1:]]:
        return {}

    fields: dict[str, object] = {}
    key = None
    mode = None          # "block" for >-/|, "list" for a `- item` sequence, "nested" for a mapping
    buffer: list[str] = []

    def flush():
        if key is None:
            return
        if mode == "list":
            fields[key] = list(buffer)
        elif mode == "block":
            fields[key] = " ".join(b.strip() for b in buffer if b.strip())

    for line in lines[1:]:
        if line.strip() == FRONTMATTER_DELIM:
            break
        stripped = line.strip()
        if not stripped:
            continue
        indented = line[:1] in (" ", "\t")

        # Continuation of a block scalar (`description: >-`), a YAML list item, or a nested mapping.
        if key is not None and mode and (indented or stripped.startswith("- ")):
            if mode == "pending":
                mode = "list" if stripped.startswith("- ") else "nested"
            if mode == "list" and stripped.startswith("- "):
                buffer.append(stripped[2:].strip().strip('"\''))
                continue
            if mode == "block" and indented:
                buffer.append(line)
                continue
            if mode == "nested":
                # A nested mapping's own keys are NOT top-level fields. Falling through stored them
                # as such, so a `metadata:` block containing `name:`/`description:` overwrote the
                # skill's real name and description in the capability table.
                continue

        # An indented `key: value` with no open block is still nested under something; skip it
        # rather than promoting it.
        if indented:
            continue

        if ":" not in line:
            continue

        flush()
        raw_key, _, value = line.partition(":")
        key, value = raw_key.strip(), value.strip()
        buffer, mode = [], None

        # `>-`, `>`, `|`, `|-` open a block scalar whose real value is on the following lines.
        # Storing the marker itself gave every folded skill the literal description ">-",
        # defeating the capability matching the table exists for.
        if value in (">", ">-", ">+", "|", "|-", "|+"):
            mode = "block"
        elif value == "":
            # An empty value opens either a YAML list (`- item`) or a nested mapping
            # (`  subkey: v`); which one is decided by the first continuation line below.
            mode = "pending"
            fields[key] = ""
        else:
            fields[key] = value.strip('"\'')
            key = None
    flush()
    return fields


def read_frontmatter(path):
    with open(path, encoding="utf-8") as f:
        return parse_frontmatter(f.read(FRONTMATTER_READ_BYTES))


def list_skills(skills_dir):
    """[(skill_name, description, path), ...] sorted by name."""
    rows = []
    for path in sorted(glob.glob(os.path.join(skills_dir, "*", "SKILL.md"))):
        fm = read_frontmatter(path)
        name = fm.get("name") or os.path.basename(os.path.dirname(path))
        rows.append((name, fm.get("description", ""), path))
    return rows


def infer_covers(readme_path, covers_field):
    """`covers` frontmatter value -> list of paths, falling back to the README's parent directory
    when absent (Phase 0 rule 1).

    Accepts both YAML shapes. Every feature README in the target repo writes `covers` as a
    **block list**, and parsing only the comma-separated inline form silently produced the
    parent-dir fallback -- so a ticket touching a sibling module the README genuinely covers was
    never matched to it.
    """
    if isinstance(covers_field, (list, tuple)):
        covers = [str(c).strip() for c in covers_field if str(c).strip()]
    else:
        covers = [c.strip() for c in (covers_field or "").split(",") if c.strip()]
    if covers:
        return covers
    return [os.path.dirname(readme_path)]


def list_feature_readmes(glob_pattern, repo_root=None):
    """[(path, covers_list, generated_from_commit), ...] sorted by path.

    `path` (and the parent-dir fallback in `covers`) is relativized against
    `repo_root` first, when given, so both fields are display-ready.
    """
    rows = []
    for path in sorted(glob.glob(glob_pattern, recursive=True)):
        fm = read_frontmatter(path)
        display_path = os.path.relpath(path, repo_root) if repo_root else path
        covers = infer_covers(display_path, fm.get("covers"))
        rows.append((display_path, covers, fm.get("generated_from_commit", "")))
    return rows


def detect_pr_template(repo_root):
    """First matching PR template path (relative), or None."""
    for candidate in (".github/PULL_REQUEST_TEMPLATE.md", ".github/pull_request_template.md"):
        if os.path.isfile(os.path.join(repo_root, candidate)):
            return candidate
    return None


# ---------------------------------------------------------------------------
# Output rendering
# ---------------------------------------------------------------------------

def render(skills, readmes, pr_template):
    lines = ["## Skills", "| name | description | path |", "|---|---|---|"]
    for name, desc, path in skills:
        lines.append(f"| `{name}` | {desc} | `{path}` |")

    lines += ["", "## Feature README candidates", "| readme | covers | generated_from_commit |", "|---|---|---|"]
    for path, covers, provenance in readmes:
        lines.append(f"| `{path}` | {', '.join(covers)} | {provenance or '-'} |")

    lines += ["", "## PR template", pr_template or "none"]
    return "\n".join(lines) + "\n"


def relativize(rows, repo_root, path_index=0):
    """Rewrite the path field of each row to be relative to repo_root (display only)."""
    result = []
    for row in rows:
        row = list(row)
        row[path_index] = os.path.relpath(row[path_index], repo_root)
        result.append(tuple(row))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skills-dir", default=".claude/skills",
        help="Directory holding this tool's skill frontmatter (name/description) — "
             "NOT `.ai/skills` (the canonical content copy has no frontmatter to parse).",
    )
    parser.add_argument(
        "--feature-readme-glob",
        default="",
        help="Glob relative to repo root. Empty (default) = do not scan feature READMEs. "
             "Pass the project-profile feature_doc_glob when it is not 'none'.",
    )
    parser.add_argument("--repo-root", default=os.getcwd())
    args = parser.parse_args(argv)

    repo_root = os.path.abspath(args.repo_root)
    skills = relativize(
        list_skills(os.path.join(repo_root, args.skills_dir)), repo_root, path_index=2,
    )
    readmes = []
    if args.feature_readme_glob.strip():
        readmes = list_feature_readmes(
            os.path.join(repo_root, args.feature_readme_glob), repo_root=repo_root
        )
    pr_template = detect_pr_template(repo_root)

    print(render(skills, readmes, pr_template))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
