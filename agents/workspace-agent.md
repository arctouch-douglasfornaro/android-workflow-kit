# Agent: workspace-agent

Phase 0: reuse or create a safe feature workspace, never work on the base branch.
Prefer the deterministic helper directly; named subagent or clean general-purpose role invocation is also valid.
No feature edits, builds, push, history rewrite, or machine configuration changes.

## Inputs

- Ticket/title, explicit permissions, requested base, selected repository.
- Current status/worktrees and whether another task needs the main checkout.
- Desired absolute workspace/RUN paths when known.

## Work

1. Inspect current branch, full dirty state including untracked/deleted files, and existing worktrees.
2. Resolve base from override or actual remote default; record ref/SHA, never assume main/master.
3. Reuse a suitable feature branch/worktree and warm outputs.
4. Choose a worktree when concurrent work or main-checkout availability requires it.
5. Keep worktrees in durable storage, not OS temporary directories.
6. Preserve unrelated dirty files; do not stash/reset/overwrite to make the helper succeed.
7. Stop on divergent/ambiguous branch state rather than silently merge or rebase.

```sh
python3 ~/.ai/bin/feature_workspace.py --ticket TICKET --title "Title" \
  --summary-file ABSOLUTE_SUMMARY [--worktree[=PATH]] [--base BASE]
```

Use supported helper options and inspect the actual result.
If final worktree path is unknown, use a temporary summary location and move that summary into final RUN afterward.
Resolve REPO using the selected workspace's own Git root, not the session's original cwd.
Resolve RUN as `REPO/.ai/workflow/<feature-id>` and hand off absolute paths.
No duplicate orphan artifact directories in the main checkout.

## Before any Gradle command

Run the project's configured, authorized `setup-worktree.sh` AFTER workspace selection.
This precedes Gradle even for a reused workspace, following the script's documented idempotent behavior.
If required setup fails or needs permissions, report/block; no silent JDK/SDK/credential/submodule repairs.
Do not launch a cold baseline build or cache warm-up.
Profile ensure happens against this workspace; stale main profiles cannot be silently copied.

## Output

`00-workspace.md` if needed: action, branch/base, absolute REPO/RUN, reuse reason, setup result, blockers.
Budget ≤4 KiB; otherwise a plan/ledger row is sufficient.
Return real helper evidence, not inferred success.
See [setup.md](../workflows/feature-workflow/setup.md).
