# Current rationale

Background only; active execution rules live in [feature-workflow.md](../feature-workflow.md).
This replaces historical incident narratives, not the safety boundaries they motivated.

## Reuse facts, not stale assumptions

Automatic setup persists minimum facts so the next task does not rediscover them.
Fingerprint source inputs before reusing a profile, especially across worktrees.
Ask people only for task-needed facts the repository cannot establish.
Workspace reuse avoids avoidable cold builds; setup scripts precede Gradle to establish legitimate local prerequisites.

## Spend checks where they add information

Implementer owns ordinary tests. A specialist adds value for complex verification, not every logic change.
One batched impacted gate avoids repeated configuration and duplicate broad suites.
Cached/UP-TO-DATE results are valid when their inputs match; routinely deleting test state adds no independent reasoning.
Widen checks for specific public/DI/resource/build risks, not fear.
Targeted mutation can demonstrate non-vacuous coverage; repeating it without a mechanism change adds little.

## Keep review independent

A clean reviewer can challenge assumptions shared by design and implementation.
Renaming the main session's role does not erase its context.
Named subagents and clean general-purpose invocations are both valid host mechanisms.
If neither is available, disclosure and explicit waiver/blocking are honest; fabricated independence is not.

## Verify the artifact users will run

Static screenshots cannot prove navigation, lifecycle, gesture, or runtime payload AC.
Device availability is checked early, but the final APK is built after review and tied to source/install identity.
One session joins assertions, logs, and minimal media without repeating navigation.
A blocked required check is not a delivery success.

## Freeze evidence before shipping

Full snapshots include untracked and deleted files; committed hunks alone miss work awaiting delivery.
Documentation and formatting are source changes too, so perform them before final verification.
Any later patch needs an impacted gate, clean delta review, and targeted device revalidation when required.
Post-hook state checks prevent shipping source different from the approved tree.
State hashes establish binding and freshness; they are not an oracle for honest manual review or device claims.

## Keep execution authorized and reporting small

Auto mode removes redundant prompts within permission, not security/attribution requirements.
Unresolved high-impact questions require answers even without an interactive user.
Short evidence artifacts preserve conclusions and reproducible references, not a diary of effort.
Measured monotonic duration and unknown-as-null telemetry are more useful than estimated savings or prices.
