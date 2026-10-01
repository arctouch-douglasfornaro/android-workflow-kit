# Product Planning

Turn a vague ticket into a precise, testable PRD that the rest of the pipeline (architect, implementer, reviewer) can execute on without ambiguity.

This skill is the cognitive core of the [`designer`](../../agents/designer.md) agent in [`feature-workflow`](../../workflows/feature-workflow.md), but can also be invoked standalone when refining a ticket. (It previously backed `product-planner`, which `designer` replaced.)

---

## When to use

- A Jira ticket is one sentence, and you do not know what "done" looks like
- A stakeholder asked for a feature informally, and you need to convert it into a testable spec
- You are about to start coding and realize the acceptance criteria are not testable
- The `designer` agent invokes this skill as part of `feature-workflow`

---

## Inputs you must extract

For any ticket, before writing anything, identify:

1. **The user / persona** — who is affected by this change?
2. **The trigger** — what event or context brings the user to this feature?
3. **The outcome** — what changes in their world after this ships?
4. **The constraint** — what must stay the same (UX, perf, accessibility, contracts)?
5. **The non-goal** — what would scope-creep this ticket?

If any of these cannot be inferred from the ticket text + project context, list it as an **Open question** in the spec. Do **not** ask the user mid-workflow; document the question and proceed with a reasonable default.

---

## Output schema

A single file at `01-design.md` (or wherever the caller specifies), with these sections in this order:

```markdown
# Spec: <feature title>

## Context
<1–3 sentences: where this lives in the product, who uses it, why now>

## Goals
- <imperative verb + observable outcome>

## Acceptance Criteria
- [ ] <checkable: passes or fails objectively, no ambiguity>

## Constraints
- <technical, product, or time constraint>

## Out of scope
- <explicit non-goal>

## Open questions
- <flagged but does not block the next phase>
```

---

## Quality rules

### Goals
- **Imperative + observable.** "Add a loading state to the Workspace screen" ✅ — "Improve Workspace screen" ❌
- **At most 3.** If you have more than 3, you have either multiple tickets or sub-goals.

### Acceptance Criteria
- **Testable.** A reviewer must be able to check each box by running the app or reading the diff — not by judgment.
  - ✅ "When the screen opens and data is loading, a centered spinner is visible and no other content is rendered"
  - ❌ "Loading state looks good"
- **Behavior, not implementation.** "Title is empty while loading" ✅ — "WorkspaceUiState has a Loading variant" ❌ (that is architecture, not AC).
- **No floors.** "Code compiles", "lint passes", "tests pass" are floors enforced by the pipeline — not acceptance criteria.

### Constraints
- **Specific.** "Must not break existing edge-to-edge support" ✅ — "Don't break anything" ❌
- **Sourced.** If a constraint comes from a real artifact (a design doc, a perf budget, a compliance rule), name it. Otherwise it is an assumption — move it to Open questions.

### Out of scope
- **Explicit non-goals,** not absence of mention. "This ticket does NOT add error UI; only the loading state" ✅
- Catches scope-creep before architecture starts spending tokens on it.

### Open questions
- One per question. Phrase as a question.
- Pair each with the reasonable default you would adopt if no answer comes in time.
- The architect will either honor the default (record under Assumptions) or flag back.

---

## Workflow

### Step 1 — Read the ticket and project context

Read the raw ticket. Then, in parallel:
- `AGENTS.md` (the project's, at its repo root) for product terminology
- [`.ai/context/project-overview.md` for product context
- The current code area the ticket touches (e.g. if it mentions Workspace, skim `features/workspaces/`)

### Step 2 — Draft the 5 inputs

Write the 5 inputs (persona, trigger, outcome, constraint, non-goal) as a scratch list. Do not put this in the final spec — it is your private map.

### Step 3 — Convert to AC

For each outcome, write one or more checkable acceptance criteria. Reject any AC that requires judgment to check.

### Step 4 — Pressure-test

Before writing the file, ask yourself:
- Could two engineers read this spec and produce wildly different implementations? If yes, tighten constraints.
- Could a reviewer not tell whether AC #N is met? If yes, rewrite AC #N.
- Did I sneak in an architecture choice? Move it out — the architect picks the architecture.

### Step 5 — Write `01-design.md`

Use the schema above. Every section header present, even if a section is "None".

---

## Anti-patterns

- ❌ Spec is one paragraph of prose with no headers
- ❌ Acceptance criteria are aspirational ("delightful", "fast", "intuitive")
- ❌ Architecture leaks in ("Add a `WorkspaceLoadingState` sealed class") — that is the architect's call
- ❌ Open questions list is empty when the ticket was vague — you skipped the work
- ❌ Spec changes the user's request (adds or removes scope) without flagging it as an Open question

---

## Example: tight spec from a vague ticket

Ticket: `MOLE-143 Add loading state to workspace`

Bad spec (vague, leaks architecture):
```
# Spec
Add a loading state to the Workspace screen. Use a sealed class with Loading and Content.
Show a spinner. Tests should pass.
```

Tight spec:
```markdown
# Spec: Workspace screen loading state

## Context
The Workspace screen currently renders an empty title and empty body while
fetching workspace details. Users see a blank screen for ~500ms–2s depending
on network. This ticket introduces a visible loading state.

## Goals
- Show a clear loading indicator while workspace data is being fetched
- Keep the top bar visible during loading so the user can go back

## Acceptance Criteria
- [ ] When the screen opens, a centered spinner is visible until data loads
- [ ] The top bar (back button, options) is visible during loading
- [ ] No tab bar, no FAB, no body content is visible during loading
- [ ] After data loads successfully, the spinner is replaced by the workspace content
- [ ] If data fails to load, the spinner is hidden (error UI is out of scope)

## Constraints
- Must reuse the existing `LoadingFullScreen` composable
- Must not break edge-to-edge insets
- Must follow the project's UI conventions (design-system tokens, no hardcoded values)

## Out of scope
- Error state UI (foundation only — Error variant exists in the model but renders nothing)
- Retry action
- Skeleton placeholders for partial content

## Open questions
- Should the FAB be visible during loading? Default: no (no workspace context yet)
- Should the top bar title show "Loading…" or stay empty? Default: empty (consistent with current behavior)
```
