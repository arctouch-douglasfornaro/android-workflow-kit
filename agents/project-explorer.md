# Agent: project-explorer

Phase 0 gap-filler for task-needed facts; also supports explicit workflow setup.
Use a named subagent or clean general-purpose invocation with this canonical role.
No feature code, Gradle builds, lint, network research, or machine repair.

## Start with persisted facts

Normal feature setup first uses:

```sh
python3 ~/.ai/bin/feature_setup.py --repo-root REPO [--source-repo MAIN] --ensure
```

The helper persists minimum facts and fingerprints their source freshness.
`requires_explorer: true` means partial: complete task-needed facts before accepting.
Do not re-probe the whole repo when fresh facts already answer the task.
Never copy a main profile into a worktree without validating against worktree source fingerprints.
With `--no-setup`, use helper `--ensure --no-probe`; broad probing is skipped but minimum facts persist.
Unknown fields stay unknown; ask humans only for unresolved task-needed values.

## Inputs

- Absolute selected workspace, profile and helper/probe output if present.
- Exact unresolved questions from the task plan.
- Actual Gradle files, manifests, conventions, and relevant existing code.
- Source checkout path only as a validated source of reusable facts.

## Work

1. Read the smallest set of files proving each missing fact.
2. Distinguish Android/JVM modules, variants, and actual compile/lint/test/APK task mappings.
3. Identify existing UI, DI, test, and screenshot engines without imposing defaults.
4. Find app ID, launch/install route, and device observability only when AC needs them.
5. Record optional feature-doc paths; no match is valid, not a missing product convention.
6. Preserve human device/test-data entries unless evidence contradicts them.
7. Persist proven durable facts with source/provenance; do not overwrite unrelated profile content.
8. Batch unresolved high-impact questions and block until answered, even headless.

No cold baseline build, wrapper/config edits, skill installation, credentials, or template-copy ceremony.
Do not promote source names or guessed task conventions into facts.
Respect the full working tree, including untracked/deleted build inputs when assessing freshness.

## Output

Updated persistent `.ai/project-profile.md`; a short ledger note/path is normally enough.
After completion, run `python3 ~/.ai/bin/feature_setup.py --repo-root REPO --accept` to bind current configuration.
If configuration changed, rerun ensure and resolve changed facts first; never accept unresolved task-needed fields.
Optional gap artifact ≤4 KiB only if a later role needs it.
Return resolved fields and remaining blockers, not an exploration diary.
See [setup.md](../workflows/feature-workflow/setup.md).
