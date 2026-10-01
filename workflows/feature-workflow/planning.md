# Phase 1 — Grounded planning

Standard/full: one [designer.md](../../agents/designer.md) pass.
Express: keep the cited sibling, AC, and verification plan in `00-plan.md`; no redundant design artifact.

## Scope

- Read actual code and only matching optional project docs.
- State testable AC, constraints, out-of-scope work, changed paths/contracts, and the smallest viable approach.
- Preserve existing feature flags/defaults unless explicitly authorized otherwise.
- Cite file:line or observed commands for claims; distinguish facts from assumptions.
- Full expands only real state, contract, migration, or reachability risks.
- Do not explore the whole repo or create an implied-work encyclopedia.

## Resolve before implementation

Correct ticket/design premises with evidence: original claim → evidence → corrected statement.
External runtime observations are evidence for that observed build/configuration, not universal truth.
Batch unresolved high-impact questions once; no headless defaults. Stop until answered.
Revisit design only when answers change scope/contracts or implementer escalates.

When AC says "all/every/across", enumerate the affected set with the search command and exclusions.
For changed external inputs, specify absent/empty/malformed/valid behavior from the accessor's real contract.
Translate these boundaries into tests; do not infer that empty and null are interchangeable.
The reviewer independently reconstructs behavior first, then checks these design claims.

## Verification plan

Implementer owns normal unit tests and regression coverage.
Phase 3 is only for complexity beyond simple unit tests: concurrency, complex fixtures, cross-boundary contracts, or comparable risk.
Record why a specialist adds value; simple logic changes do not automatically spawn `test-writer`.
Express skips the specialist; escalate if complexity requires it.
`--no-tests` skips NEW authoring by implementer and specialist, NEVER required regression execution.
Log coverage gaps; independent review may block missing essential tests rather than silently overriding the flag.

Choose the cheapest evidence that can falsify each AC:
- Existing unit/integration coverage for source-verifiable behavior.
- Existing screenshot engine for static pixels only; never install a screenshot library for the workflow.
- Interactive device for runtime emissions, navigation, gesture, animation, lifecycle, or hardware AC.
- NOT_REQUIRED only when every AC is provable otherwise, with evidence/rationale.

Name impacted compile/lint/test tasks from the proven profile.
Widen for changed public APIs, DI, resources, schema, or build inputs; not all downstream modules by habit.
Plan documentation edits before the final gate snapshot.

## Output

`01-design.md`: AC, evidence-backed approach, boundaries, verification, and unresolved questions only.
≤8 KiB; full ≤12 KiB. Omit unused sections.
Return a one-line status and artifact path; no compile/lint or duplicated source listings.
