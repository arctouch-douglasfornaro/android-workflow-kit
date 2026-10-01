# Phase 4 — One impacted quality gate

Orchestrator owns compile + lint + mandatory regression execution, before independent review.
Agents do not each compile/lint. Useful narrow development runs are evidence to reuse, not discard.

## Snapshot and scope

Use the selected workspace and resolved base from Phase 0.
All stages inspect the full diff: branch changes, staged/unstaged edits, untracked source, and deletions.
Never use `git diff base...HEAD` alone; it omits work awaiting commit.
Include tests, resources, build inputs, and changed project docs; exclude generated outputs/run artifacts explicitly.

```sh
python3 ~/.ai/bin/feature_state.py --repo-root REPO --base PINNED_BASE_SHA snapshot
```

Keep that snapshot identity with gate, review, and device evidence.
File-hash cache validation includes ctime; reuse warm snapshots without bypassing source checks.
Clean initialized submodules are supported; dirty/uninitialized submodules block. Preserve work and request authorized setup, never reset them.
Every state command explicitly passes global `--repo-root REPO --base PINNED_BASE_SHA` before its subcommand; snapshot does not pin subsequent calls.
Finish feature docs and any formatting edits BEFORE snapshot/gate; later source edits invalidate approvals.
Protect unrelated user changes; never silently drop them from inspection or stage them as feature work.

Select compile/lint/test tasks from the proven profile's module/variant map.
Android and JVM tasks differ; never invent a task by substituting a module name.
Run changed-module checks and widen for public API, DI, resources, schema, build/config, or evidenced consumer risk.
Do not blanket compile/lint/test the whole repo or require a final full-repository suite.
If the initial gate already covers final impact, it IS the final gate.

## Execute once

Check task-relevant mechanical conventions, then batch compile + lint + tests in ONE Gradle invocation.
Use only supported profile flags; omit a check only if genuinely inapplicable, with a reason.
Wrap the complete gate command; convention failure must propagate as nonzero, not be hidden by the final Gradle exit.

```sh
python3 ~/.ai/bin/feature_state.py --repo-root REPO --base PINNED_BASE_SHA run --run-dir RUN --phase gate --log RUN/gate.log -- COMMAND ...
```

`COMMAND ...` is the actual project gate command/batch, not a placeholder to execute literally.
Gate PASS is produced only by exit 0 with the same source fingerprint before/after.
Never manually `record` a gate PASS; the API accepts gate FAIL/BLOCKED, but only `run` produces gate PASS.
Capture the command's real exit status; pipes must not replace it with a log filter's success.
Convention wrappers must translate matches into failures: raw grep exit 0 means a match, not a clean check.

Cache hits and UP-TO-DATE compile/lint/test results are valid for matching inputs.
No routine `cleanTest*`, `clean`, or `--rerun-tasks`; investigate actual cache corruption/input-tracking faults before invalidating.
Reuse narrow development evidence for matching suites/sources; the batch can consume Gradle's valid cached results.
Retain task names, selected scope, exit status, source identity, report paths, and measured duration.
Read counts from available test reports; unknown counts stay unknown. No invented totals or timestamp-only cache rejection.
`--no-tests` does not disable mandatory regression tasks.
It skips NEW test authoring by both authors; log the coverage gap. Missing essential tests may still block review.

## Targeted falsification

Use regression tests that would fail without the change.
When a mechanism needs explicit mutation proof, do it once with the narrow affected suite.
Back up exact current files first; restore from verified copies, never Git reset/restore/stash.
Verify byte-for-byte restoration and green targeted results.
Reuse this evidence unless the mechanism changes; do not mutate again on every gate.
No failing assertion after breaking the mechanism means coverage is insufficient, not approval.

## Failure, review, and reuse

Wrong task/invocation: orchestrator fixes the command, not feature code.
Compile/test/convention/lint failures: send concise failure evidence to implementer.
Do not repeatedly re-prove known unrelated failures; record them, but never turn a failed mandatory gate into PASS.
Bound substantive and mechanical retries; stop with unresolved blockers at the configured limit.

After a PASS, launch the clean reviewer; reviewer reads evidence and code, not another Gradle run.
Missing/red gate blocks review rather than transferring gate execution to the reviewer.
After any fix: impacted gate → clean-context delta review → targeted device, when required.
Expand once for newly identified final impact; do not rerun an unchanged sufficient final gate.

```sh
python3 ~/.ai/bin/feature_state.py --repo-root REPO --base PINNED_BASE_SHA check --run-dir RUN --phase gate
```

State freshness is necessary, not proof that scope or manual claims were honest.
Record a short ledger row with raw log/report paths; do not paste build output into review artifacts.
