# Phase 0 — Setup and triage

Goal: a reusable workspace, persistent proven facts, resolved blockers, and a small `00-plan.md`.
Do not start a cold baseline build or duplicate the designer's feature exploration.

## Workspace first

1. Read ticket, permissions, flags, current status, worktrees, and base ref/SHA.
2. Reuse a suitable existing feature branch/worktree. Preserve unrelated changes; never overwrite branches.
3. Prefer warm in-place work when safe; use a worktree for concurrent work or to keep the main checkout free.
4. Use `feature_workspace.py` directly or [workspace-agent.md](../../agents/workspace-agent.md).
5. Worktrees belong in durable storage, not OS temporary directories.
6. Resolve `REPO` from the selected workspace's Git root and `RUN` as its absolute `.ai/workflow/<feature-id>/`.
7. Write artifacts there, not in the session's original checkout; pass absolute paths to every role.
8. Run the project's configured, authorized `setup-worktree.sh` AFTER workspace selection and BEFORE any Gradle invocation.
9. If that setup is missing when required, fails, or needs new permissions, report/block; do not silently repair the machine.

Do only task-needed preflight: wrapper/JDK/SDK availability, required submodules, identity and auth for enabled delivery.
Read configuration first; do not run Gradle merely to warm the cache.
No global JDK/SDK/Git edits, credential changes, hook bypasses, or destructive resets.

## Automatic persistent profile

Normal first-run/refresh path:

```sh
python3 ~/.ai/bin/feature_setup.py --repo-root REPO [--source-repo MAIN] --ensure
```

`MAIN` is the source checkout when using a worktree, not an authority to overwrite local facts.
The helper persists minimum profile facts and validates freshness by source fingerprints.
Read JSON `requires_explorer`: true means partial, not ready; complete task-needed facts with project-explorer.
After completion, run `python3 ~/.ai/bin/feature_setup.py --repo-root REPO --accept` to bind the current configuration.
If configuration changed during exploration, rerun ensure and resolve the changed facts before accept.
Reuse fresh facts. A source profile is reusable only after checking its fingerprint against the selected worktree.
Never silently copy a stale source profile or treat timestamps alone as freshness proof.
If the helper is unavailable or fails, stop and report the contract gap; do not claim setup succeeded.

With `--no-setup` / `--no-bootstrap`, run `python3 ~/.ai/bin/feature_setup.py --repo-root REPO --ensure --no-probe`.
This skips broad probing but persists minimum facts; resolve task-needed gaps and accept only after completion.
Mark partial/unresolved fields honestly. This flag does not allow a missing profile or guessed tasks.
No human form-filling upfront: ask only for unresolved fields needed by this task.
Group high-impact questions once; unanswered questions block even in auto/headless mode.

Persist, as needed:
- Actual Android/JVM module and variant task mappings for compile, lint, tests, and APK assembly.
- Existing lint/test/screenshot engines; `none` is valid when verified.
- App ID, install/launch route, device/AVD configuration, and observability needed by runtime AC.
- Relevant conventions and optional feature-documentation paths.
- Proven durable environment constraints, without secrets or task-specific incident narratives.

Use [project-explorer.md](../../agents/project-explorer.md) only for gaps requiring code judgment.
Do not generate wrappers, install skills, change configs, or copy many empty template artifacts.
Keep local profiles/run artifacts out of feature staging unless separately authorized.

## Capabilities and early device preflight

Use actual host tool/subagent capabilities, not a hardcoded registry path.
A named subagent or clean general-purpose invocation with the canonical role is valid; Firebender supports subagents.
Do not run `check_registry.py` by default. Verify clean reviewer isolation is possible now.
If unavailable, disclose and block or request explicit waiver; waiver is not a normal review PASS.
Load only relevant discovered skills and conventions. Missing optional skills/docs do not block.

For runtime AC, check connected devices early; otherwise start only a configured, authorized AVD.
No AVD wiping, app-data clearing, account creation, or credentials without permission.
A required unavailable device is a blocker to resolve early, not a later delivery caveat.

## Premises and plan

For UI/flags/navigation, prove the entry point and reachability from code; ticket text is not proof.
Preserve existing feature-flag behavior unless explicitly in scope.
Optionally make ONE early baseline observation only if it resolves an important premise and a usable baseline is already installed.
Record baseline identity and limits; it is not final AC verification. Never cold-build just for capture.
If an important premise remains unresolved, ask once and stop rather than assuming.

Write `00-ticket.md` with AC and `00-plan.md` with:
- Workspace, absolute RUN, resolved base, permissions/flags, profile pointer/freshness.
- Track and cited sibling or complexity signal.
- Planned phases, specialist-test rationale, runtime/static verification needs, and risk-based gate scope.
- Reachability and unresolved questions; optional evidence paths only when used.

Optional workspace/profile/capability/ground-truth artifacts exist only if useful to a later role.
Create the full-diff snapshot, including untracked/deleted source, using the state helper documented in [gate.md](gate.md).
Phase 0 is complete only with a persisted minimum profile and resolved task-blocking questions.
