# Phase 8 — Final consistency check

Finalize is a check, not a mandatory new edit/build cycle.
Documentation maintenance belongs before the final source snapshot, normally during Phase 2.

## Documentation

- Read only project docs matched by the profile or directly affected by the change.
- Update only descriptions the feature makes incorrect: contracts, entry points, flags, ownership, or usage.
- No matched docs means no invented README and no required empty `08-readme.md`.
- Document shipped behavior; keep unverified assumptions and external problems in evidence/carryover.
- Never fabricate provenance SHAs or stamp a base commit as the verified feature.
- Run a required documentation validator as part of the impacted gate, not a late unrecorded check.

A short Phase 8 ledger row records which docs were updated earlier or why none needed changes.
Use `08-readme.md` only for a decision a later role needs.

## Freeze, do not polish

Check the full source snapshot against gate, review, and device evidence, including untracked/deleted files.
No unconditional formatting, lint, broad tests, or APK rebuild after approval.
If final impact was already covered and sources are unchanged, reuse the final evidence.
If documentation/source changes now, invalidate prior approvals and return:
impacted gate → clean-context delta review → targeted device if required.
If only gate scope was incomplete, run the one missing final impacted gate and refresh dependent evidence.
Do not convert a late "small fix" into an exception to revalidation.

```sh
python3 ~/.ai/bin/feature_state.py --repo-root REPO --base PINNED_BASE_SHA check --run-dir RUN
```

Default check covers gate, review, and device.
Require gate PASS, actual isolated review PASS, and device PASS or justified NOT_REQUIRED.
Required BLOCKED/FAIL, missing evidence, or stale fingerprints stop delivery.
An explicit isolation waiver is not an isolated review PASS; do not forge state to pass shipping checks.

## Handoff

Carry only actual deferred/nonblocking findings, authorized scope changes, and verification limitations.
Do not create carryover or skip artifacts when a ledger row suffices.
Confirm delivery permissions/flags already recorded; auto mode does not ask repeatedly.
Proceed to [delivery.md](delivery.md) only with current evidence and authorized actions.
