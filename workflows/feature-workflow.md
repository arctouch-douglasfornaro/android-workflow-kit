# Feature workflow

Speed-first Android delivery: reuse project facts, implement once, verify the actual final tree, and ship only within explicit user permissions.
Project facts: `<repo>/.ai/project-profile.md`. Kit index: [ANDROID.md](../ANDROID.md).
Higher-priority host instructions, security rules, attribution requirements, and approval boundaries always win.

## Invocation and permissions

`<workflow-trigger> <ticket-id> <title or description> [flags]`

| Flag | Effect |
|---|---|
| `--no-auto` | Human approval after plan/design, review, required device pass, and before enabled delivery. Never skips verification. |
| `--max-iterations N` | Bound fix loops; default 5. Stop with unresolved failures at the limit. |
| `--track express\|standard\|full` | Override triage; still honor hard stops and escalate discovered complexity. |
| `--no-tests` | Skip NEW test authoring by implementer and specialist, NEVER required regression gate execution. Log coverage gaps; review may block missing essential tests. |
| `--no-setup` | Skip automatic probe, but persist minimum proven project facts. Alias: `--no-bootstrap`. |
| `--base BRANCH` | Override detected base. |
| `--no-commit` | Stop before commit; implies `--no-push` and `--no-pr`. |
| `--no-push` | Commit only; implies `--no-pr`. |
| `--no-pr` | Commit and push only. |

Auto is the default execution mode, not permission to commit, push, publish, or bypass host approvals.
Establish delivery permissions once; proceed without repeated confirmation within those permissions.
Batch unresolved high-impact questions once. If unanswered, STOP, including headless runs; never invent a default.
Feature ID: `<TICKET-ID>-<lowercase-slug>` (slug at most 60 characters); no ticket: `LOCAL-<MMDD>-<slug>`.
Base: explicit override, else remote default, else existing `origin/main` or `origin/master`; unresolved base blocks.
Resolve and record the base ref and SHA; never silently change the comparison base mid-run.

## Tracks

| Track | Selection | Design / tests |
|---|---|---|
| express | Clear AC, cited shipped sibling, localized module, no novel public contract/state model/module | Skip designer; implementer owns tests. |
| standard | Default; bounded change needing one grounded design | One concise design; implementer owns normal tests. |
| full | New module, public/cross-module contract, novel state model, new surface, or unresolved product premise changing AC | Deeper design of actual risks, not speculative scope. |

Uncertainty alone does not require full. Record the selection in `00-plan.md`.
Specialist `test-writer` is only warranted beyond simple unit tests (e.g. concurrency, complex fixtures, cross-boundary behavior).
Express skips that specialist; escalate track if complexity warrants one. `--no-tests` never weakens the gate.
Independent review runs on EVERY track. Device necessity follows AC, not track or file extension.
Preserve existing feature flags and their defaults unless the task explicitly authorizes changing them.

## Phase map (stable 0–9)

| # | Owner / action | Evidence | Detail |
|---|---|---|---|
| 0 | Orchestrator: workspace, setup, capability/preflight, triage | `00-ticket.md`, `00-plan.md`; optional useful setup artifacts | [setup.md](feature-workflow/setup.md) |
| 1 | `designer`, standard/full or escalation | `01-design.md` | [planning.md](feature-workflow/planning.md) |
| 2 | `implementer`: code, normal tests, required docs | `02-implementation.md` | [implementer.md](../agents/implementer.md) |
| 3 | `test-writer`, only warranted specialist work | `03-tests.md` if used | [test-writer.md](../agents/test-writer.md) |
| 4 | Orchestrator: batched impacted compile + lint + tests | state and raw gate log | [gate.md](feature-workflow/gate.md) |
| 5 | Clean independent `feature-reviewer` | `05-review.md` | [feature-reviewer.md](../agents/feature-reviewer.md) |
| 6 | Implementer fix loop | compact deltas, refreshed evidence | Rules below |
| 7 | `device-pass` when required; otherwise justified NOT_REQUIRED | `07-device.md` and state | [device.md](feature-workflow/device.md) |
| 8 | Final consistency check, no unconditional edits | ledger row; artifact only if useful | [finalize.md](feature-workflow/finalize.md) |
| 9 | Ship helper / `ship-agent`, then `pr-author` | shipping summary, PR body/URL if enabled | [delivery.md](feature-workflow/delivery.md) |

Phase 8's documentation work is prepared in Phase 2, before the final snapshot; late changes invalidate evidence.
No mandatory cold baseline build. No compile/lint in every agent. No routine broad test reruns.
One final impacted gate only if evidence is missing or invalidated; reuse unchanged final results.

## Setup contract

After selecting/reusing the workspace, run its authorized `setup-worktree.sh` before any Gradle command.
Automatically ensure a profile on first run:

```sh
python3 ~/.ai/bin/feature_setup.py --repo-root REPO [--source-repo MAIN] --ensure
```

The helper persists minimum facts and fingerprints their sources for freshness.
Read `requires_explorer`; when true, complete task-needed facts with project-explorer, then run `feature_setup.py --repo-root REPO --accept` to bind the current configuration.
Never blindly copy a main-checkout profile into a worktree: validate it against worktree sources.
Humans fill only unresolved task-needed fields. Map `--no-setup` to `--ensure --no-probe`: skip broad probing, not persistence.
Reuse a suitable branch/worktree and warm outputs; never silently repair JDK, SDK, credentials, or Git configuration.
Preflight required device access early: connected device or configured, authorized AVD; no wiping or credential invention.
An early capture is optional only to settle an important premise using an already-installed usable baseline.
Never cold-build an app solely for that capture.

## Roles and independence

A named subagent OR a clean general-purpose invocation loaded with the canonical role is valid.
Firebender supports subagents. Use the actual host capabilities; do not assume a `.claude` registry or run `check_registry.py` by default.
Pass absolute workspace/artifact paths and only task-relevant skills. No agent transcripts or repeated full-context handoffs.
Reviewer must run in clean context, never the main session pretending to forget.
If isolation is unavailable, disclose and block or obtain an explicit waiver; never silently approve.
A waiver is a disclosed exception, not an isolated review PASS, and cannot satisfy normal automated shipping checks.

Reviewer initially gets ticket/AC, base, snapshot/full diff, proven profile facts, conventions, and gate evidence.
Exclude design, implementation/test narratives, PR copy, and prior conclusions from the initial code-first pass.
After freezing independent findings, reviewer may read design (express plan) for compliance.
On a fix, pass finding IDs plus actual delta; review affected boundaries afresh, without the author's narrative.

## Snapshot and state contract

Every stage uses the full source snapshot: committed changes from the resolved base, staged/unstaged changes, untracked files, and deletions.
Never rely on `git diff base...HEAD` alone. Include build inputs, resources, tests, and changed project docs.
Generated outputs/run artifacts are not feature source; disclose exclusions and do not hide untracked source.
Snapshot caching includes ctime in file-hash validation; clean initialized submodules are supported, dirty/uninitialized submodules block.
Every state command must explicitly pass global `--repo-root REPO --base PINNED_BASE_SHA` before its subcommand. Snapshot does NOT initialize/pin later commands.

```sh
python3 ~/.ai/bin/feature_state.py --repo-root REPO --base PINNED_BASE_SHA snapshot
python3 ~/.ai/bin/feature_state.py --repo-root REPO --base PINNED_BASE_SHA run --run-dir RUN --phase gate --log RUN/gate.log -- COMMAND ...
python3 ~/.ai/bin/feature_state.py --repo-root REPO --base PINNED_BASE_SHA record --run-dir RUN --phase review --status PASS --expected-fingerprint START_FP --evidence RUN/05-review.md
python3 ~/.ai/bin/feature_state.py --repo-root REPO --base PINNED_BASE_SHA check --run-dir RUN
```

Gate PASS requires command exit 0 and unchanged source fingerprint.
Review PASS requires an actual isolated reviewer artifact; only record the verdict it supports.
Capture START_FP from snapshot BEFORE each independent review/device assessment, not at recording time.
Manual review/device PASS and device NOT_REQUIRED require `--expected-fingerprint START_FP`; source changes since start are rejected. FAIL/BLOCKED require none.
Review PASS requires fresh gate; device PASS/NOT_REQUIRED and device `run` require fresh gate AND review.
Device uses `run --phase device` only when the command asserts AC AND APK identity; otherwise record after observing evidence.
`record` accepts gate/review/device and PASS, FAIL, BLOCKED, NOT_REQUIRED; gate PASS is rejected (use `run`), and only device may be NOT_REQUIRED.
Device NOT_REQUIRED requires a rationale; review is never NOT_REQUIRED.
`check` defaults to all stages; repeat `--phase gate`, `--phase review`, or `--phase device` to select stages.
State hashes bind evidence to sources; scripts cannot prove honesty or completeness of manual review/device artifacts.
Shipping requires `--run-dir RUN` for non-dry-run; pass `--preserve-attribution` when the host requires attribution.

## Fix loop and stops

Gate failure, required AC failure, or blocking review finding returns to implementer; design gaps return to designer first.
After ANY fix: impacted gate → clean-context delta review → targeted device verification when required.
Even layout-only fixes need delta review. Rebuild/reinstall if APK identity changed; never deliver an unrevalidated patch.
Widen by public API, DI, resources, schema, or build risk, not blanket repository scope.
Mutation checks are targeted when useful; reuse evidence unless the mechanism changes.
Nonblocking review findings may be explicitly deferred; do not loop for optional polish.
Count substantive fix rounds; log invocation errors separately and bound mechanical retries too.
Stop on unresolved high-impact questions, missing required facts/evidence, red gate, blocked review, required device FAIL/BLOCKED, exhausted retries, or unsafe delivery.
Device verdicts: PASS / FAIL / BLOCKED / NOT_REQUIRED only. No degraded-to-delivery path.
Changing required device scope needs explicit authorization and a revised plan; never relabel an unverified AC PASS.
On stop, preserve work, log the reason and last good phase, and report what unblocks progress.

## Evidence and completion

Store only used artifacts in the workspace's absolute `.ai/workflow/<feature-id>/` directory.
Ordinary structured artifacts ≤4 KiB; design/review ≤8 KiB, full-track design/review ≤12 KiB.
Raw logs/media stay in files, linked rather than pasted. No empty artifact checklist.
Use one task-log row per phase/attempt and short decision/evidence references: [logging.md](feature-workflow/logging.md).
Measure wall duration with a monotonic clock; unknown tokens are null, not estimated.
No speculative savings percentages or pricing research.
Final report: track, changed files, verification and snapshot, review/device outcome, deferred gaps, authorized delivery result, measured duration/tokens if known.
