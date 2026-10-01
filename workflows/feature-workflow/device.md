# Phase 7 — Targeted device evidence

Owner: [device-pass.md](../../agents/device-pass.md), after a passing gate and clean independent review.
Preflight device availability in Phase 0, not after investing in a final APK.

## Choose evidence by AC

| AC | Required path |
|---|---|
| Source-verifiable logic | Tests/code evidence; device NOT_REQUIRED with rationale. |
| Static pixels | Existing screenshot engine if sufficient; otherwise device. |
| Runtime output, navigation, motion, gestures, lifecycle, hardware | Interactive device session with targeted assertions. |

Use a static screenshot engine only when already configured. No new screenshot libraries.
A Preview alone does not prove runtime AC.
An existing passing screenshot task may be reused for matching sources; do not rerun merely to make a device artifact.
Device NOT_REQUIRED means ALL AC are provable otherwise, with evidence and rationale, not "no device available".

## Preflight and identity

- Reuse the connected device; otherwise start only a configured, authorized AVD.
- Never wipe the AVD, clear app data, change credentials, or invent account access without permission.
- Missing required access is BLOCKED; stop or obtain explicit changed-scope authorization.
- An optional early capture can settle an important premise using an installed usable baseline.
- Never cold-build solely for baseline capture or mistake baseline evidence for final verification.

After review, build the APK ONCE using the profile task, unless an already-built APK matches the reviewed source snapshot.
Record source fingerprint, build command/variant, APK path, SHA-256 digest, package/version, and device serial.
Prove the installed application is that APK: capture install result and installed artifact identity.
A filename, version label, or install exit 0 alone does not prove the installed digest matches.
For split APKs, record and verify the installed set's identities.
If identity cannot be established, report BLOCKED, never verify an unknown/stale binary.
A build that changes source invalidates gate/review; return through verification before device assertions.

## One session

1. Reuse any valid early recipe/baseline observation; discover the actual entry point from code.
2. Preserve feature-flag defaults; authorized test toggles must be explicit and recorded.
3. Exercise highest-risk premises first, then remaining runtime AC.
4. Capture same-session logs and targeted assertions (exact payload/count/state where relevant).
5. Prove the flow reached the target: a missing event on an unvisited screen is not a successful negative assertion.
6. Export at most ONE still per changed surface and ONE short clip per gesture behavior.
7. Keep raw logs/media in RUN files; reference them rather than pasting or capturing screenshots of logs.

Prefer existing flows; manual driving is valid when it yields observable assertions.
Do not add reusable flow infrastructure just to satisfy the workflow.
Record preconditions, route, expected/observed result per AC, identity proof, and verdict in `07-device.md` (≤4 KiB).
An outside-diff issue is only proven pre-existing with baseline evidence; do not use that label to excuse failed required AC.

## State recording

Before device assessment (including NOT_REQUIRED), capture START_FP from:

```sh
python3 ~/.ai/bin/feature_state.py --repo-root REPO --base PINNED_BASE_SHA snapshot
```

Device PASS/NOT_REQUIRED and device `run` require fresh gate AND review.
Manual success requires `--expected-fingerprint START_FP`; sources changed since assessment start are rejected.
FAIL/BLOCKED need no expected fingerprint. Never capture a replacement START_FP after verification to hide changes.
Every call explicitly supplies root/pinned base; snapshot alone does not pin later commands.
Automated device PASS is valid only when the command asserts BOTH AC and APK/install identity:

```sh
python3 ~/.ai/bin/feature_state.py --repo-root REPO --base PINNED_BASE_SHA run --run-dir RUN --phase device --log RUN/device.log -- COMMAND ...
```

An install command or generic flow exit 0 alone is insufficient. For manual observation:

```sh
python3 ~/.ai/bin/feature_state.py --repo-root REPO --base PINNED_BASE_SHA record --run-dir RUN --phase device --status PASS --expected-fingerprint START_FP --evidence RUN/07-device.md
python3 ~/.ai/bin/feature_state.py --repo-root REPO --base PINNED_BASE_SHA record --run-dir RUN --phase device --status NOT_REQUIRED --expected-fingerprint START_FP --evidence RUN/07-device.md --reason "All AC proven by cited non-device evidence"
```

Use the actual result, never pre-record success. State hashes bind artifacts; they cannot establish honesty of manual claims.

## Verdict and fix loop

Only PASS / FAIL / BLOCKED / NOT_REQUIRED. No DEGRADED delivery state.
FAIL returns to implementer. Required BLOCKED halts delivery; a PR checklist is not a substitute.
Changed-scope authorization must explicitly remove/revise the blocked AC and update the plan, without faking PASS.
After ANY patch, including layout-only: impacted gate → clean delta review → targeted device.
Build/install a new matching APK when sources changed; target failed and plausibly affected AC.
Reuse unaffected assertions only with a recorded impact rationale and current snapshot/identity evidence.
Never deliver an unrevalidated device fix.
