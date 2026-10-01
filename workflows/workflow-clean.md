# Workflow: workflow-clean

Deletes **run artifacts** (logs, media, probe dumps) so a repo is not cluttered after feature-workflow runs. Does not delete source, the project profile, or git history.

```
/workflow-clean [<feature-id> | --all] [--worktrees]
```

| Argument | Effect |
|---|---|
| *(none)* | Ask which run, or list `.ai/workflow/*` directories (except `_setup` / `_bootstrap`) and delete the most recent if the human confirms in auto mode **do not guess** — if unattended, require `--all` or an id. Interactive: list and wait. Auto with no args: print the list and stop (do not delete). |
| `<feature-id>` | Delete `.ai/workflow/<feature-id>/` only. |
| `--all` | Delete every `.ai/workflow/<feature-id>/` plus `_setup/` and `_bootstrap/` probe dumps. |
| `--worktrees` | Report stale feature worktrees and prune the ones git already considers gone. Never combined implicitly — pass it. |

---

## Always preserve

- `<repo>/.ai/project-profile.md`
- `<repo>/.ai/conventions/` (when it exists)
- `<repo>/AGENTS.md`
- anything outside `.ai/workflow/`
- git objects / working tree source

`--purge-profile` is **not** implemented. Wiping the profile is `/workflow-setup --force` after a human edit, not clean.

---

## Steps

1. Resolve repo root. Refuse if `.ai/workflow` does not exist: "nothing to clean."
2. Build the delete list from the argument. Never `rm -rf` the repo root. Never `git clean`.
3. Delete only those directories. Report paths removed.
4. If a directory is missing, say so and continue.

### Worktrees (`--worktrees`)

Runs accumulate worktrees nobody removes: on one machine, thirteen — three of them `prunable`
(the directory gone, the registration left behind) and six with no `.ai/workflow/` at all, so their
run artifacts had either never been written there or were written into the main checkout instead.

```bash
git worktree list --porcelain
```

Classify each, report all, and act only where it is safe:

| State | Action |
|---|---|
| `prunable` — directory gone | `git worktree prune`. Removes only the stale registration; the branch is untouched. |
| Directory present, branch merged into the base, tree clean | **Report only.** Removing a worktree is the human's call — say the branch is merged and give them the `git worktree remove` command. |
| Directory present, uncommitted changes or unmerged branch | **Report and stop.** Never remove. This is someone's unshipped work — one run deliberately left a whole diff uncommitted in a worktree pending review. |

Branches are never deleted, and a worktree with a dirty tree is never touched. The run artifacts
inside a worktree follow the same rules as anywhere else: only `.ai/workflow/` contents, only on an
explicit id or `--all`.

## Does not

- ❌ `git reset` / `git clean`
- ❌ Delete `project-profile.md`
- ❌ Touch `~/.ai`
- ❌ Delete a git branch
- ❌ Remove a worktree with uncommitted changes, or any worktree without being asked
