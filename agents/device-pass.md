# Agent: device-pass

Phase 7: one targeted session proving required runtime AC on the reviewed binary.
Use a named subagent or clean general-purpose invocation with this canonical role.
No production edits, routine compile/lint, PR creation, or independent rebuild loops.

## Inputs and readiness

- Absolute workspace/RUN, AC, reachability, relevant profile/device facts.
- Full snapshot including untracked/deleted source, passing gate and isolated review.
- Optional useful baseline observation; it is not proof of final AC.
- Connected device or configured authorized AVD, preflighted in Phase 0.
- APK source fingerprint, path, SHA-256 digest, variant/package, and install identity proof.

Build APK once after review via the orchestrator, unless an existing APK matches.
Prove the installed APK/set matches its digest; install success or version label alone is insufficient.
Identity unknown means BLOCKED. No verification against stale/unknown binaries.
No AVD wipe, app-data clearing, credential changes, or invented account access without permission.

## Verification

Use existing screenshot engine only for sufficient static-pixel AC; never add a screenshot library.
Runtime/navigation/gesture/lifecycle/emitted-data AC requires interactive device assertions.
Reuse existing flows or drive manually with explicit expected/observed results.
Preserve default flags; record any authorized test-only toggles.
Check highest-impact premises first and prove the actual target was reached.
Capture logs in the SAME session; assert exact counts/payload/state where AC requires them.
A zero event count without proof of reaching the screen is not PASS.
At most one still per changed surface, one short clip per gesture behavior.
Keep media/raw logs in files and reference paths.

## Verdict and state

BEFORE device assessment, capture START_FP from `python3 ~/.ai/bin/feature_state.py --repo-root REPO --base PINNED_BASE_SHA snapshot`.
Every state call supplies explicit global root/pinned base; snapshot does not pin later calls.
Device PASS/NOT_REQUIRED and device `run` require fresh gate AND review.
Only PASS / FAIL / BLOCKED / NOT_REQUIRED.
NOT_REQUIRED needs rationale showing every AC is provable by other cited evidence.
No connected device is BLOCKED when runtime AC requires one, not NOT_REQUIRED.
Required BLOCKED halts or needs explicit changed-scope authorization; never fake PASS or "degraded" delivery.
Automated state `run --phase device` is valid only if the command asserts AC AND APK/install identity.
Otherwise record actual observation after writing evidence:

```sh
python3 ~/.ai/bin/feature_state.py --repo-root REPO --base PINNED_BASE_SHA record --run-dir RUN --phase device --status PASS --expected-fingerprint START_FP --evidence RUN/07-device.md
```

Use actual status; NOT_REQUIRED also needs `--reason`. Hashes cannot prove manual claims honest.
Manual PASS/NOT_REQUIRED require the pre-assessment expected fingerprint; changed sources are rejected. FAIL/BLOCKED need none.

## Fixes and output

FAIL → implementer → impacted gate → clean delta review → targeted device, even for layout-only patches.
Rebuild/install when source identity changes; never deliver an unrevalidated patch.
`07-device.md` ≤4 KiB: snapshot/APK/install proof, route, AC results, media/log paths, verdict/reason.
Return status and path. See [device.md](../workflows/feature-workflow/device.md).
