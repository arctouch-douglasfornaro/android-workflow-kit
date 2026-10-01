#!/usr/bin/env python3
"""Deterministic, token-free helper for committing and pushing in feature-workflow.

Performs all deterministic Git delivery operations:
  * Validates we are on a valid feature branch (refuses base branch like master/main);
  * Stages every change EXPLICITLY -- tracked modifications, deletions, renames, and untracked
    files -- excluding `.ai/workflow/` artifacts, and prints exactly what it staged. Never
    `git add -u` (omits untracked files) and never `git commit -a`;
  * Refuses to commit when there is nothing to stage, rather than adopting HEAD;
  * Refuses to stage while merge conflicts are unresolved;
  * Trims an over-long subject on a word boundary and warns; refuses an attribution-only subject;
  * Strips only attribution-SHAPED lines by default; --preserve-attribution honors host policy;
  * Creates the commit;
  * Pushes to origin/<branch> (unless --no-push);
  * Writes the `09-ship.md` artifact directly.

Actual delivery requires --run-dir and current feature_state gate/review/device receipts.
Dry-run is an unvalidated preview, never a valid delivery. User authorization belongs to the
orchestrator, not this script. Host attribution policies override the stripping default.
`--paths` may not omit dirty source: isolate unrelated work before validation and delivery.
After commit, compare raw content/modes/path sets against the validated worktree, HEAD and index,
not the old HEAD-bound fingerprint. Hooks/filters that change source block push (also --no-push
success); the local commit is left untouched for inspection. No concurrent writers are supported.
Source hashing and clean-submodule checks belong exclusively to feature_state's cached snapshots.
Recovery: inspect a blocked local commit, rerun gate/review/device on its exact HEAD, then use
--resume-commit FULL_SHA --run-dir RUN (plus --no-push for summary only). Never rewrites history.

Saves ~15k-30k tokens per run by eliminating the need for an LLM agent spawn
for purely mechanical Git operations.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import subprocess
import sys

import feature_state

# Matches only ATTRIBUTION-SHAPED LINES, anchored at the start of the line, so ordinary prose that
# happens to contain a phrase like "generated with the doc skill" is never stripped. An unanchored
# `generated\s+with` once deleted a whole subject line, leaving git to promote a body line and lose
# the ticket prefix.
ATTRIBUTION_PATTERN = re.compile(
    r"^\s*(?:"
    r"co-authored-by:\s*.*(?:claude|chatgpt|copilot|gemini|antigravity|cursor|bot|"
    r"cloud\s*code|codex|grok|anthropic|openai)"
    r"|(?:🤖\s*)?generated\s+with\s+\[?(?:claude|chatgpt|copilot|gemini|antigravity|"
    r"cursor|cloud\s*code|codex|grok)"
    r"|made(?:\s+with|-with:)\s+\[?(?:cloud\s*code|claude|chatgpt|copilot|gemini|cursor|codex)"
    r"|🤖\s*generated\s+with"
    r"|assisted-by:"
    r")",
    re.I | re.M,   # re.M is load-bearing: the pattern is ^-anchored and is also applied to the
                   # joined multi-line message as a final safety net, which without it could only
                   # ever match the first line.
)


def git_result(args: list[str], repo_root: str) -> subprocess.CompletedProcess:
    # Match feature_state's repository scope; retain environment-only author identity.
    env = os.environ.copy()
    allowed = {"GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_NOSYSTEM",
               "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_AUTHOR_DATE",
               "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL", "GIT_COMMITTER_DATE"}
    for key in tuple(env):
        if key.startswith("GIT_") and key not in allowed:
            del env[key]
    env.update(GIT_TERMINAL_PROMPT="0", GIT_NO_REPLACE_OBJECTS="1")
    return subprocess.run(
        ["git", "--no-pager", "--literal-pathspecs", *args], cwd=repo_root,
        capture_output=True, text=True, errors="surrogateescape", env=env,
    )


def git(args: list[str], repo_root: str, check: bool = True) -> str:
    res = git_result(args, repo_root)
    if check and res.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed:\n{res.stderr.strip()}")
    return res.stdout.strip()


def resolve_base_branch(repo_root: str) -> str:
    ref = git(["symbolic-ref", "refs/remotes/origin/HEAD"], repo_root, check=False)
    if ref:
        return ref.removeprefix("refs/remotes/origin/")
    for candidate in ("main", "master"):
        if git(["rev-parse", "--verify", f"refs/remotes/origin/{candidate}"], repo_root, check=False):
            return candidate
    return "master"


def current_branch(repo_root: str) -> str:
    return git(["rev-parse", "--abbrev-ref", "HEAD"], repo_root)


def write_summary(repo_root, run_dir, name, text=None):
    """Validate in every mode and again at write time; atomic replacement avoids hardlink writes."""
    root = Path(repo_root)
    directory = feature_state._run_dir(root, run_dir or ".ai/workflow/preview")
    target = Path(name) if os.path.isabs(name) else root / name
    if not run_dir:
        directory = root / ".ai/workflow"
        feature_state._run_dir(root, directory / "preview")
    target = feature_state._artifact(directory, target)
    if target.name == "_state-cache.json":
        raise ValueError("summary cannot overwrite the state cache")
    if text is not None:
        feature_state._atomic(target, text.encode("utf-8"))


def strip_attribution(text: str) -> str:
    lines = []
    for line in text.splitlines():
        if ATTRIBUTION_PATTERN.search(line):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def format_commit_message(ticket_id: str, subject: str, body: str | None = None,
                          preserve_attribution: bool = False) -> str:
    ticket_clean = ticket_id.strip()
    subj_clean = subject.strip()
    
    # Boundary-aware: plain `startswith` treated `MF-233` as already present in
    # "MF-2333: ..." , so no prefix was added and the commit carried the wrong ticket.
    already_present = bool(ticket_clean) and re.match(
        re.escape(ticket_clean) + r"(?![0-9])", subj_clean, re.I) is not None
    if ticket_clean and not already_present:
        full_subject = f"{ticket_clean} {subj_clean}"
    else:
        full_subject = subj_clean

    # Strip attribution BEFORE capping, and never return an empty subject: an emptied subject makes
    # git promote the first body line, silently losing the ticket prefix.
    stripped_subject = full_subject if preserve_attribution else strip_attribution(full_subject)
    if not stripped_subject.strip():
        raise ValueError(
            f"subject is attribution-only and would be emptied: {full_subject!r}"
        )
    full_subject = stripped_subject

    # Cap at 72 chars on a WORD boundary, and say so — a silent mid-word cut mangles git history.
    if len(full_subject) > 72:
        cut = full_subject[:72].rstrip()
        if " " in cut and not full_subject[72:73].isspace():
            cut = cut.rsplit(" ", 1)[0].rstrip()
        print(
            f"warning: subject exceeded 72 chars and was trimmed to a word boundary:\n"
            f"  before: {full_subject}\n  after:  {cut}",
            file=sys.stderr,
        )
        full_subject = cut

    if not body or not body.strip():
        return full_subject

    body_clean = body.strip() if preserve_attribution else strip_attribution(body.strip())
    return f"{full_subject}\n\n{body_clean}\n"


def render_ship_artifact(sha: str, subject: str, files: list[str], push_target: str,
                         conflicts: str = "none", push_error: str | None = None,
                         validation: str = "validation not performed; not a valid delivery") -> str:
    file_list = "\n".join(f"- {f}" for f in files) if files else "- (none)"
    return f"""# Ship

## Validation
{validation}

## Commit SHA
{sha}

## Subject
{subject}

## Files in commit
{file_list}

## Push
{push_target}

## Instruction conflicts
{conflicts}

## Push error
{push_error or "none"}
"""


# Run artifacts and the mid-run profile only. `.ai/` itself is a TRACKED, team-owned tree in real
# repos (35 files here: `.ai/rules/`, `.ai/skills/`, `.ai/prompts/`, `.ai/guidelines.md`), so
# excluding the whole prefix silently dropped a ticket's own edits to those files -- and a
# skills-only ticket exited with "nothing to commit".
EXCLUDED_PREFIXES = (".ai/workflow/",)
EXCLUDED_PATHS = tuple(feature_state.EXCLUDED)


class StagePlan:
    """What the ship step will and will not stage, decided from porcelain output.

    `to_stage` is what `git add` receives; `commit_pathspec` is what `git commit -- …` receives.
    They differ for renames, and conflating them shipped a broken commit -- see `commit_pathspec`.
    """

    def __init__(self) -> None:
        self.to_stage: list[str] = []
        self.commit_pathspec: list[str] = []
        self.excluded: list[str] = []
        self.conflicted: list[str] = []


def _porcelain_all_paths(entry: str) -> list[str]:
    """Both sides of a rename/copy record; the single path otherwise.

    The commit pathspec needs the OLD path too. A pathspec commit records only changes matching the
    pathspec, so passing new-only silently dropped the deletion half of a rename: the commit then
    contained both files -- a duplicate class in Kotlin, so the pushed commit did not compile -- and
    left `D <old>` staged in the tree.
    """
    return [pth for pth in entry[3:].split("\x00") if pth]


def _porcelain_paths(entry: str) -> list[str]:
    """Extract the path(s) to stage from one `git status --porcelain -z` record.

    Never slice a fixed offset off the display form (`R  old -> new`): that records the arrow as
    part of a filename and undercounts the change by one per rename.

    A rename/copy record carries `new\0old`. Only **new** is returned: the old path no longer
    exists on disk and is already gone from the index as part of the rename, so passing it to
    `git add` fails with `pathspec 'old' did not match any files`.
    """
    code = entry[:2]
    fields = [pth for pth in entry[3:].split("\x00") if pth]
    if code[:1] in ("R", "C") and fields:
        return fields[:1]
    return fields


def _porcelain_records(repo_root: str) -> list[str]:
    """`git status --porcelain -z` records, rename/copy entries joined with their old path.

    The NUL form is the only safe one: the display form C-quotes any path with a space or
    non-ASCII character and renders a rename as `R  "old" -> "new"`, so string comparison against
    real paths produces false mismatches.
    """
    result = git_result(["status", "--porcelain", "-z", "--untracked-files=all"], repo_root)
    result.check_returncode()
    raw = result.stdout
    records: list[str] = []
    chunks = raw.split("\x00")
    i = 0
    while i < len(chunks):
        chunk = chunks[i]
        if not chunk:
            i += 1
            continue
        if chunk[:1] in ("R", "C"):
            records.append(chunk + "\x00" + (chunks[i + 1] if i + 1 < len(chunks) else ""))
            i += 2
        else:
            records.append(chunk)
            i += 1
    return records


def in_progress_operation(repo_root: str) -> str | None:
    """`merge`, `rebase`, `cherry-pick`, `revert`, or None."""
    git_dir = git(["rev-parse", "--git-dir"], repo_root)
    git_dir = git_dir if os.path.isabs(git_dir) else os.path.join(repo_root, git_dir)
    for marker, name in (("MERGE_HEAD", "merge"), ("rebase-merge", "rebase"),
                         ("rebase-apply", "rebase"), ("CHERRY_PICK_HEAD", "cherry-pick"),
                         ("REVERT_HEAD", "revert")):
        if os.path.exists(os.path.join(git_dir, marker)):
            return name
    return None


def porcelain_lines(repo_root: str) -> list[str]:
    """`git status --porcelain` records with their fixed-width prefix intact.

    Never read porcelain through `git()`: that strips the whole stdout, so the leading space of the
    first record disappears (` M a.kt` -> `M a.kt`). An unstaged file then reads as staged and its
    path is sliced one character short.
    """
    res = git_result(["status", "--porcelain"], repo_root)
    res.check_returncode()
    return [l for l in res.stdout.split("\n") if l]


def collect_paths_to_stage(repo_root: str, only: list[str] | None = None) -> StagePlan:
    """Every changed path, tracked or untracked, minus workflow artifacts.

    `git add -u` is deliberately not used: it omits untracked files entirely, so a feature whose
    diff is mostly new files commits without them.
    """
    plan = StagePlan()
    # Porcelain paths are repo-relative, but the rest of the kit insists on absolute paths, so an
    # absolute --paths would match nothing and every change would land in `excluded` -- failing with
    # "nothing to commit", which points at the wrong cause. Normalize instead.
    if only:
        normalized = []
        for raw_path in only:
            if os.path.isabs(raw_path):
                rel = os.path.relpath(raw_path, repo_root)
                if rel.startswith(".."):
                    raise ValueError(f"--paths entry is outside the repo: {raw_path}")
            else:
                rel = os.path.normpath(raw_path)
            # `relpath`/`normpath` render the repo root as "." and no porcelain path equals "." or
            # starts with "./", so keeping it would exclude every change and fail with "nothing to
            # commit" -- the wrong cause, which is what this normalization exists to avoid.
            if rel in (".", ""):
                normalized = []
                break
            normalized.append(rel)
        only = normalized or None

    for rec in _porcelain_records(repo_root):
        code = rec[:2]
        for pth in _porcelain_paths(rec):
            if "U" in code or code in ("AA", "DD"):
                plan.conflicted.append(pth)
            elif pth.startswith(EXCLUDED_PREFIXES) or pth in EXCLUDED_PATHS:
                plan.excluded.append(pth)
            elif only and not any(pth == o or pth.startswith(o.rstrip("/") + "/") for o in only):
                plan.excluded.append(pth)
            else:
                plan.to_stage.append(pth)
                # `git add` errors on a rename's old path (it is gone from disk and the index), but
                # `git commit --` needs it to record the deletion. Two lists, one decision.
                plan.commit_pathspec.extend(_porcelain_all_paths(rec))

    for attr in ("to_stage", "commit_pathspec", "excluded", "conflicted"):
        setattr(plan, attr, sorted(set(getattr(plan, attr))))
    return plan


def is_git_repo(repo_root: str) -> bool:
    res = git_result(["rev-parse", "--is-inside-work-tree"], repo_root)
    return res.returncode == 0


def source_index(repo_root: str) -> dict:
    entries = {}
    for row in feature_state._git(repo_root, "ls-files", "--stage", "-z").split(b"\0"):
        if not row:
            continue
        meta, path = row.split(b"\t", 1)
        mode, oid, stage = meta.decode().split()
        if stage != "0":
            raise ValueError("unresolved Git conflicts")
        path = os.fsdecode(path)
        if not feature_state._excluded(path):
            entries[path] = (mode, oid)
    return entries


def source_tree(repo_root: str, commit: str) -> dict:
    return {p: entry for p, entry in feature_state._tree(repo_root, commit).items()
            if not feature_state._excluded(p)}


def working_changes(snapshot: dict, head_source: dict) -> set[str]:
    """Raw worktree/HEAD differences already computed by the cached state snapshot.

    A base-only deletion tombstone has no corresponding HEAD entry and is not a
    worktree difference. Every other override (including gitlinks and modes) is.
    Never substitute porcelain/diff here: index flags and filters can hide edits.
    """
    return {p for p, entry in snapshot["working_overrides"].items()
            if entry["mode"] != "deleted" or p in head_source}


def verify_committed_source(repo_root: str, before: dict, ready: dict,
                            selected: list[str], resume: bool = False) -> str:
    after = feature_state.snapshot(repo_root, ready["base_commit"])
    if after["branch"] != before["branch"]:
        raise ValueError("branch changed during delivery")
    parents = git(["rev-list", "--parents", "-n", "1", "HEAD"], repo_root).split()
    if (resume and after["head"] != before["head"]) or (
            not resume and parents != [after["head"], before["head"]]):
        raise ValueError("delivery must create exactly one commit on validated HEAD")
    committed_paths = feature_state._names(feature_state._git(
        repo_root, "diff-tree", "--no-commit-id", "--name-only", "--no-renames",
        "-r", "-z", before["head"], after["head"]))
    if not resume and committed_paths - set(selected):
        raise ValueError("commit contains paths outside the staging plan")
    if after["content_fingerprint"] != ready["content_fingerprint"]:
        raise ValueError("source content fingerprint changed after validation (possibly a hook)")
    # Snapshot compares raw worktree bytes/modes with HEAD, bypassing clean filters
    # and assume-unchanged flags. Equal content alone would miss a hook changing
    # the committed blob then restoring the validated worktree.
    objects = source_tree(repo_root, after["head"])
    if working_changes(after, objects):
        raise ValueError("committed source does not match validated worktree")
    if source_index(repo_root) != objects:
        raise ValueError("staged source does not match validated worktree")
    # Redundant for ordinary files, but makes the selected-path HEAD/worktree invariant explicit.
    if selected:
        git(["diff", "--exit-code", "--no-ext-diff", "--no-textconv", "HEAD", "--", *selected],
            repo_root)
    # Only metadata queries after the single post-hook snapshot; no second source
    # traversal or independent rehash. As with state receipts, concurrent writers
    # are unsupported, but reject observable HEAD/branch/index races.
    if (git(["rev-parse", "HEAD"], repo_root) != after["head"] or
            current_branch(repo_root) != after["branch"] or source_index(repo_root) != objects):
        raise ValueError("Git state changed during post-commit checks")
    return after["head"]


def main(argv: list[str] | None = None) -> int:
    try:
        return _main(argv)
    except Exception as exc:
        # Malformed receipts, unreadable source and Git/hook failures all fail closed.
        # Never undo a commit or continue to push after a failed safety check.
        print(f"error: delivery blocked: {exc}\n"
              "Inspect local HEAD/index/worktree; no automatic repair was attempted.", file=sys.stderr)
        return 1


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deterministic commit & push for feature-workflow")
    parser.add_argument("--ticket", required=True, help="Ticket ID (e.g. ABC-123 or LOCAL)")
    parser.add_argument("--subject", required=True, help="Commit subject line")
    parser.add_argument("--body", help="Commit body text (optional)")
    parser.add_argument("--body-file", help="Path to file containing commit body (optional)")
    parser.add_argument("--preserve-attribution", action="store_true",
                        help="Keep attribution when required by host policy (overrides default stripping)")
    parser.add_argument("--run-dir", help="Required for actual delivery: validated feature_state run")
    parser.add_argument("--resume-commit", help="Deliver exact current full SHA after fresh validation; no commit")
    parser.add_argument("--base", help="Override base branch")
    parser.add_argument("--no-push", action="store_true", help="Commit only, do not push")
    parser.add_argument("--summary-file", help="Path to write 09-ship.md summary artifact")
    parser.add_argument("--repo-root", default=os.getcwd(), help="Repository root path")
    parser.add_argument("--dry-run", action="store_true",
                        help="Unvalidated preview only; never a valid delivery")
    parser.add_argument(
        # nargs="+" on purpose: with "*", a bare `--paths` (an empty shell variable expanding away)
        # yields [], which is falsy and skips the filter entirely -- staging every dirty file in the
        # tree, the exact outcome this flag exists to prevent. "+" makes that an argparse error.
        "--paths", nargs="+", default=None,
        help="Restrict staging to these paths/prefixes. Omit to stage every change outside "
             "generated workflow/profile artifacts. Dirty source outside these paths blocks delivery; "
             "isolate unrelated work first.",
    )
    args = parser.parse_args(argv)
    if args.resume_commit and (args.dry_run or args.paths):
        raise ValueError("--resume-commit cannot be combined with --dry-run or --paths")

    validation = "validation not performed; not a valid delivery"
    if args.dry_run:
        print(f"[dry-run] {validation}")
    elif not args.run_dir:
        raise ValueError("--run-dir is required for actual delivery")

    repo_root = str(Path(args.repo_root).resolve())
    if not is_git_repo(repo_root):
        print(f"error: '{repo_root}' is not inside a git repository.", file=sys.stderr)
        return 1

    if args.summary_file:
        write_summary(repo_root, args.run_dir, args.summary_file)
    base = resolve_base_branch(repo_root)
    branch = current_branch(repo_root)
    supplied = git(["rev-parse", "--symbolic-full-name", "--verify", "--end-of-options",
                    args.base], repo_root, check=False) if args.base else ""
    supplied = (supplied.split("/", 3)[-1] if supplied.startswith("refs/remotes/")
                else supplied.removeprefix("refs/heads/"))
    if branch in ("main", "master", base, supplied, "HEAD"):
        print(f"error: refused to ship on base/detached branch '{branch}'. Must be on a feature branch.", file=sys.stderr)
        return 1

    body_content = args.body or ""
    if args.body_file:
        # Resolve against the repo root, not the process cwd: on a worktree run a relative path
        # would point at the wrong tree. Silently falling back to an empty --body produced a
        # subject-only commit with nothing in the output saying the body had been dropped.
        body_path = args.body_file if os.path.isabs(args.body_file) else os.path.join(repo_root, args.body_file)
        if not os.path.isfile(body_path):
            print(f"error: --body-file not found: {body_path}", file=sys.stderr)
            return 1
        with open(body_path, "r", encoding="utf-8") as f:
            body_content = f.read()

    commit_msg = format_commit_message(args.ticket, args.subject, body_content,
                                       preserve_attribution=args.preserve_attribution)

    # Check for attribution trailers
    if not args.preserve_attribution and ATTRIBUTION_PATTERN.search(commit_msg):
        print("warning: attribution trailer detected and stripped from commit message.", file=sys.stderr)
        commit_msg = strip_attribution(commit_msg)

    # Stage explicitly. `git add -u` was wrong twice over: it silently omits UNTRACKED files (a
    # ticket that adds new Kotlin files would commit without them and not compile) and it sweeps in
    # every unrelated tracked modification in the tree.
    try:
        paths = collect_paths_to_stage(repo_root, args.paths)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    in_progress = in_progress_operation(repo_root)
    if in_progress:
        print(
            f"error: a {in_progress} is in progress. This step commits with a pathspec, which git "
            "refuses as a partial commit while that state exists.\n"
            f"       Finish or abort the {in_progress} first, then re-run.",
            file=sys.stderr,
        )
        return 1

    if paths.conflicted:
        print(
            "error: unresolved merge conflicts; refusing to stage:\n  "
            + "\n  ".join(paths.conflicted),
            file=sys.stderr,
        )
        return 1

    if not paths.to_stage and not args.resume_commit:
        print(
            "error: nothing to commit. The working tree has no feature changes outside the "
            "excluded paths.\n"
            "       Refusing to adopt HEAD as this run's commit — a run that produced no diff must "
            "not report a green delivery over the previous commit.",
            file=sys.stderr,
        )
        return 1

    # Anything already in the index that this run did not choose is a foreign staged change: an
    # aborted earlier ship, an agent's stray `git add -A`, a human's `git add -p`. The commit below
    # is pathspec-scoped so it cannot swallow them, but they are worth surfacing.
    staged_now = set()
    for rec in _porcelain_records(repo_root):
        if rec[:1] in ("M", "A", "D", "R", "C"):
            staged_now.update(_porcelain_paths(rec))
    preexisting = sorted(staged_now - set(paths.to_stage))
    if preexisting:
        print(
            "notice: these paths were already staged and are NOT part of this commit:\n  "
            + "\n  ".join(preexisting)
        )

    print(f"staging plan: {len(paths.to_stage)} path(s):")
    for pth in paths.to_stage:
        print(f"  {pth}")
    if paths.excluded:
        print(f"excluded {len(paths.excluded)} path(s) by policy/--paths:")
        for pth in paths.excluded:
            print(f"  {pth}")

    if not args.dry_run:
        # Validate before ANY index/commit mutation. Fingerprints include HEAD, so retain a
        # separate effective-content identity to bridge the intended commit transition.
        ready = feature_state.verify_ready(repo_root, args.run_dir, base=args.base)
        before = feature_state.snapshot(repo_root, ready["base_commit"])
        if (before["fingerprint"] != ready["fingerprint"] or
                before["content_fingerprint"] != ready["content_fingerprint"]):
            raise ValueError("source changed after receipt validation")
        head_source = source_tree(repo_root, before["head"])
        dirty = working_changes(before, head_source)
        index_source = source_index(repo_root)
        dirty |= {p for p in set(head_source) | set(index_source)
                  if head_source.get(p) != index_source.get(p)}
        if args.resume_commit:
            if args.resume_commit != before["head"] or dirty:
                raise ValueError("resume requires exact current full SHA and clean source/index")
        omitted = dirty - set(paths.commit_pathspec)
        if omitted:
            raise ValueError("dirty source excluded by --paths/status; isolate unrelated work before "
                             "validation and delivery: " + ", ".join(sorted(omitted)))
    if not args.dry_run and not args.resume_commit:
        # `-A` so a deletion inside the pathspec is staged as a deletion rather than skipped.
        git(["add", "-A", "--", *paths.to_stage], repo_root)

        # Scoped to the paths just added: a pre-existing staged file (§ the notice above) would
        # otherwise satisfy this check even when `add` staged nothing, and the pathspec commit would
        # then die as an unhandled error instead of aborting cleanly here.
        staged_now = set()
        for rec in _porcelain_records(repo_root):
            if rec[:1] in ("M", "A", "D", "R", "C"):
                staged_now.update(_porcelain_paths(rec))
        staged_lines = sorted(staged_now & set(paths.to_stage))
        if not staged_lines:
            print("error: `git add` staged nothing from the chosen paths; aborting rather than "
                  "committing an empty or unrelated tree.", file=sys.stderr)
            return 1
    else:
        staged_lines = []

    if args.dry_run:
        commit_sha = "dry-run-sha-00000000"
        changed_files = list(paths.to_stage)
        print(f"[dry-run] Would commit {len(changed_files)} files with subject: {commit_msg.splitlines()[0]}")
    elif args.resume_commit:
        commit_sha = verify_committed_source(repo_root, before, ready, [], resume=True)
        commit_msg = git(["log", "-1", "--format=%B", commit_sha], repo_root)
        changed_files = sorted(feature_state._names(feature_state._git(
            repo_root, "diff-tree", "--no-commit-id", "--name-only", "-r", "-z", commit_sha)))
        validation = f"PASS: fresh receipts {ready['fingerprint']}; resumed approved HEAD"
    else:
        # Pathspec-scoped commit. A plain `git commit -m` commits the WHOLE INDEX, so every path
        # this run deliberately excluded -- `.ai/workflow/` artifacts, `--paths` rejects, anything
        # staged before the run -- shipped anyway while the output above claimed it was excluded.
        # Naming the paths makes the printed staging list and the commit the same set.
        git(["commit", "-m", commit_msg, "--", *paths.commit_pathspec], repo_root)
        attempted_sha = git(["rev-parse", "HEAD"], repo_root)
        changed_files = sorted(feature_state._names(feature_state._git(
            repo_root, "diff-tree", "--no-commit-id", "--name-only", "-r", "-z", "HEAD")))
        try:
            commit_sha = verify_committed_source(repo_root, before, ready, paths.commit_pathspec)
        except Exception as exc:
            recovery = (f"BLOCKED: {exc}\nInspect {attempted_sha}; rerun gate/review/device on current "
                        f"HEAD, then --resume-commit {attempted_sha} --run-dir {args.run_dir}. "
                        "Do not rerun ordinary ship; no push occurred.")
            print(recovery, file=sys.stderr)
            write_summary(repo_root, args.run_dir,
                          args.summary_file or str(feature_state._run_dir(Path(repo_root), args.run_dir) / "09-ship.md"),
                          render_ship_artifact(attempted_sha, commit_msg.splitlines()[0],
                                               changed_files, "BLOCKED — not pushed", validation=recovery))
            raise
        validation = f"PASS: receipts {ready['fingerprint']}; committed source matches validated worktree"

    subject_line = commit_msg.splitlines()[0] if commit_msg else args.subject

    push_target = "skipped (--no-push)"
    push_error = None
    if not args.no_push and not args.dry_run:
        try:
            git(["push", "-u", "origin", branch], repo_root)
            push_target = f"origin/{branch} @ {commit_sha}"
        except RuntimeError as exc:
            # The commit already exists. Letting this propagate skipped the summary write below, and
            # a re-invocation then refused with "nothing to commit" because the tree was clean --
            # leaving the run unrecoverable and `pr-author` without its only sourceable artifact.
            # Record the commit, report the push failure, exit non-zero.
            push_error = str(exc).strip()
            push_target = f"FAILED — commit {commit_sha} exists locally but was not pushed"
    elif args.dry_run and not args.no_push:
        push_target = f"[dry-run] origin/{branch} @ {commit_sha}"

    if args.summary_file:
        write_summary(repo_root, args.run_dir, args.summary_file,
                      render_ship_artifact(commit_sha, subject_line, changed_files, push_target,
                                           push_error=push_error, validation=validation))

    print(f"commit_sha: {commit_sha}")
    print(f"subject: {subject_line}")
    print(f"push: {push_target}")
    print(f"files_changed: {len(changed_files)}")

    if push_error:
        print(
            f"error: push rejected. The commit is safe at {commit_sha[:8]}; only the push failed.\n"
            f"{push_error}\n"
            "       Resolve the remote divergence yourself (this step never force-pushes and never\n"
            "       rebases). Revalidate current HEAD, then use --resume-commit with its full SHA.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
