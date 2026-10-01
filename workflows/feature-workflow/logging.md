# Logging and evidence

One task-log row per phase/attempt; structured artifacts only when used by a later role.
Keep reasoning in its artifact, not duplicated into logs, prompts, and final reports.

## Run location and ledger

Use absolute `RUN=<selected workspace>/.ai/workflow/<feature-id>/`.
Record feature ID, workspace, base ref/SHA, track, permissions/flags, and source snapshot identity once.

| Phase | Attempt | Status | Wall seconds | Evidence / one-line note |
|---|---|---|---|---|
| 4 | 1 | PASS | measured | gate.log; snapshot identity |
| 5 | 1 | PASS | measured | 05-review.md; isolated invocation ID |
| 7 | 1 | NOT_REQUIRED | measured | 07-device.md; rationale |

Use actual values, not these example labels. Skipped optional phases need only a reason in the ledger.
On failure record the blocker, last good phase, and raw error path; preserve work.
Do not manufacture many empty `00-*`, test, README, or carryover files.

## Budgets

- Ordinary structured artifact: ≤4 KiB.
- Design/review: ≤8 KiB; full-track design/review: ≤12 KiB.
- PR authored prose: template + 2000 bytes, or 2000 bytes without a template; host requirements take precedence.
- Raw build/test/device logs and media live in separate files, not pasted into these budgets.
- Keep the active ledger short; link attempt evidence rather than accumulating copied command output.

Check artifact byte sizes before handoff. Cut duplicate narrative, source dumps, and search diaries.
Keep verdict, evidence path, exact command/exit, file:line, limitation, and next action.
Pass paths and narrow excerpts; never read full agent transcripts.
Media: at most one still per changed surface and one short clip per gesture behavior.

## Evidence state

```sh
python3 ~/.ai/bin/feature_state.py --repo-root REPO --base PINNED_BASE_SHA snapshot
python3 ~/.ai/bin/feature_state.py --repo-root REPO --base PINNED_BASE_SHA run --run-dir RUN --phase gate --log RUN/gate.log -- COMMAND ...
python3 ~/.ai/bin/feature_state.py --repo-root REPO --base PINNED_BASE_SHA record --run-dir RUN --phase review --status PASS --expected-fingerprint START_FP --evidence RUN/05-review.md
python3 ~/.ai/bin/feature_state.py --repo-root REPO --base PINNED_BASE_SHA check --run-dir RUN
```

Full snapshot includes untracked/deleted source, not only committed branch hunks.
Explicit global `--repo-root REPO --base PINNED_BASE_SHA` is required on EVERY call; snapshot does not pin later calls.
Capture START_FP from snapshot BEFORE each independent review/device assessment.
Manual review/device PASS and device NOT_REQUIRED need `--expected-fingerprint START_FP`; changed sources are rejected. FAIL/BLOCKED need none.
Review PASS requires fresh gate; device PASS/NOT_REQUIRED and device `run` require fresh gate plus review.
Hash-cache validation includes ctime; clean initialized submodules work, dirty/uninitialized ones block.
`run`: gate/device only; gate PASS needs exit 0 and unchanged fingerprint.
Device command must assert AC and APK/install identity, otherwise record observed evidence manually.
`record`: gate/review/device; PASS / FAIL / BLOCKED / NOT_REQUIRED with optional `--reason`.
Gate PASS is rejected (use `run`); gate FAIL/BLOCKED may be recorded. Only device may be NOT_REQUIRED.
Review PASS requires real isolated review; review NOT_REQUIRED is not allowed by this workflow.
Device NOT_REQUIRED requires a reason and evidence showing AC are provable otherwise.
`check` defaults to all; repeat `--phase` to check selected stages.
Record after evidence exists. Never synthesize a success artifact just to satisfy hashes.
Hashes prove binding/freshness, not the honesty or adequacy of manual review/device assertions.

## Timing and metrics

Read UTC timestamps for start/end labels; measure elapsed wall time with a monotonic clock.
Use the same live timer/process or actual tool-reported duration; never subtract unrelated process clocks or invent missing intervals.
Total wall time is not the sum of overlapping agent durations.
On resume, report measured segments and unknown gaps rather than fabricating continuous elapsed time.

Keep `metrics.json` small if used:

```json
{"durationSeconds": null, "phases": [], "tokens": null, "tokenSource": null, "terminal": "BLOCKED"}
```

Populate only measured values. `null` means unknown; `0` means measured zero.
Tokens come only from actual host telemetry; no estimates from phase count or artifact length.
Record status and known metrics on every exit. No speculative savings percentages, invented cost, or pricing web research.
