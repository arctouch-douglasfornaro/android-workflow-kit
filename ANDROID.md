# Android workflow kit

Universal Android/Gradle workflow; project-specific facts live in `<repo>/.ai/project-profile.md`.
Read repository conventions when present. Never invent a product layout, DI framework, lint engine, or design system.
Higher-priority host security and permissions always win. Commit messages and PR
bodies are product copy only: no agent or tool attribution (`Generated with`,
`Made with Cloud Code`, agent `Co-Authored-By`). Follow the repository PR/MR
template when it exists; 2–3 lines of prose; do not invent extra headings.

## Start a task

`/feature-workflow <ticket-id> <description>` selects a workspace and automatically ensures minimum persistent facts:

```sh
python3 ~/.ai/bin/feature_setup.py --repo-root REPO [--source-repo MAIN] --ensure
```

`/android-workflow <ticket-id> <description>` is the host-driven T0–T9 sibling: the coding agent implements; `python3 ~/.ai/bin/android-workflow` detects, locates, gates, and refuses `finish` without a source diff. Canonical: [android-workflow.md](workflows/android-workflow.md).

No manual setup prerequisite for the first feature.
The helper fingerprints source freshness; never blindly copy a stale main-checkout profile into a worktree.
When `requires_explorer` is true, complete task-needed profile facts, then run `feature_setup.py --repo-root REPO --accept`.
Reuse fresh facts; humans supply only unresolved task-needed fields.
`--no-setup` maps to helper `--ensure --no-probe`: skip broad probing, not minimum-profile persistence.
Explicit `/workflow-setup` and `/android-bootstrap` remain available for broader setup work.
Use `/workflow-help` for guidance and `/workflow-clean` for authorized run-artifact cleanup.

## Execution defaults

- Reuse suitable branches/worktrees and build outputs; no mandatory cold baseline build.
- Run configured, authorized `setup-worktree.sh` after workspace selection and before Gradle.
- No silent machine repairs, credentials, destructive resets, or library installation.
- Auto proceeds within explicit permissions; unresolved high-impact questions block even headless.
- Named subagent or clean general-purpose invocation with the canonical role is valid.
- Firebender supports subagents; use actual host capability, not a presumed `.claude` registry.
- Independent reviewer runs on every track, in clean context; unavailable isolation needs disclosed block/explicit waiver, never fake approval.
- Implementer owns normal tests; specialist test-writer only for complexity beyond simple unit tests.
- One batched impacted compile/lint/regression gate; matching cache results are valid.
- `--no-tests` skips NEW authoring by implementer and specialist, NEVER required regression execution; log gaps, and review may block missing essential tests.
- Required runtime AC needs a device; static screenshot verification uses only an existing engine.
- Device PASS / FAIL / BLOCKED / NOT_REQUIRED; required BLOCKED never degrades into delivery.
- Final APK source identity, digest, and install proof accompany same-session AC evidence.
- Any fix returns through impacted gate, clean delta review, then targeted device when required.
- Documentation precedes final snapshot. Reuse final unchanged results; no unconditional last formatting/test pass.

## Tracks and flags

Express: cited sibling and localized known pattern; skip designer/specialist, not independent review.
Standard: one concise grounded design; default when uncertain.
Full: explicit novel module/state/contract/surface or product risk; deeper design only where needed.
Preserve existing feature flags/defaults unless the task authorizes changes.

Core flags: `--track`, `--base`, `--no-auto`, `--max-iterations` (default 5), `--no-tests`, `--no-setup` (`--no-bootstrap`).
Delivery cascade: `--no-commit` → `--no-push` → `--no-pr`; flags never grant missing permissions.

## Canonical file map (phases stay 0–9)

| Phase | Workflow / role |
|---|---|
| All | [feature-workflow.md](workflows/feature-workflow.md) |
| Alt | [android-workflow.md](workflows/android-workflow.md), [skill](skills/android-workflow/SKILL.md), [playbook](skills/android-workflow/references/playbook.md) |
| 0 | [setup.md](workflows/feature-workflow/setup.md), [workspace-agent.md](agents/workspace-agent.md), [project-explorer.md](agents/project-explorer.md) |
| 1 | [planning.md](workflows/feature-workflow/planning.md), [designer.md](agents/designer.md) |
| 2 | [implementer.md](agents/implementer.md) |
| 3 | [test-writer.md](agents/test-writer.md), conditional specialist |
| 4 | [gate.md](workflows/feature-workflow/gate.md) |
| 5 | [feature-reviewer.md](agents/feature-reviewer.md), always isolated |
| 6 | Fix loop in [feature-workflow.md](workflows/feature-workflow.md) |
| 7 | [device.md](workflows/feature-workflow/device.md), [device-pass.md](agents/device-pass.md) |
| 8 | [finalize.md](workflows/feature-workflow/finalize.md) |
| 9 | [delivery.md](workflows/feature-workflow/delivery.md), [ship-agent.md](agents/ship-agent.md), [pr-author.md](agents/pr-author.md) |
| Evidence | [logging.md](workflows/feature-workflow/logging.md) |
| Background | [rationale.md](workflows/feature-workflow/rationale.md) |

## State and evidence

`feature_state.py` binds gate/review/device evidence to a full source snapshot, including untracked/deleted files.
Its file-hash cache validates ctime too; clean initialized submodules work, dirty/uninitialized submodules block.
Never use committed-only `git diff base...HEAD` as the whole change.
Gate PASS requires command exit 0 and unchanged fingerprint; review PASS requires a real isolated artifact.
Every state call explicitly includes global `--repo-root REPO --base PINNED_BASE_SHA` before its subcommand; snapshot does not pin later calls.
Capture START_FP BEFORE independent review/device; manual PASS and device NOT_REQUIRED need `--expected-fingerprint START_FP`, rejecting source changes since start.
Review PASS requires fresh gate; device PASS/NOT_REQUIRED and device `run` require fresh gate plus review. FAIL/BLOCKED need no expected fingerprint.
Device automated PASS requires AC and APK identity assertions; manual records require actual observations.
Device NOT_REQUIRED needs a rationale proving AC otherwise.
`feature_ship.py --run-dir RUN` is required for non-dry-run shipping and checks state before commit and after hooks before push.
Pass shipping `--preserve-attribution` when host policy requires attribution.
Hashes cannot prove honesty of manual artifacts. Never forge state to bypass an isolation/device blocker.

Ordinary artifacts ≤4 KiB; design/review ≤8 KiB, full ≤12 KiB.
Use only useful artifacts and task-log rows; keep raw logs/media in files.
Measure monotonic wall duration; tokens null when unknown; no speculative savings or pricing research.
Tool wrappers/configs remain thin; canonical behavior lives here, not in copied host-specific instructions.
