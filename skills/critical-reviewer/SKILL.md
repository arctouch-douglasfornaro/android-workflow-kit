# Critical Reviewer

A critical, deep review of the current branch's changes **before** opening a PR.
Acts as a pessimistic senior reviewer — assume there is a bug, side effect, or convention violation until proven otherwise.

## Independence rule

Review **code before narrative**. Do not read architecture documents, implementation summaries,
test summaries, PR descriptions, or previous review conclusions before completing the initial
change-surface, call-site, and side-effect investigation.

Those artifacts describe intent and can anchor the review to an incorrect premise. Reconstruct
behavior from Git, production code, actual tests, and repository call sites first. After findings
are drafted, product requirements may be read to check scope and acceptance criteria;
author-written implementation summaries are never evidence that behavior is correct.

---

## When to use

- Before opening a PR ("review before push", "check before PR", "self-review")
- After a large implementation session, to validate what was done
- When you suspect a change has cross-module impact
- When the diff touches sensitive areas (DI modules, navigation, mappers, shared ViewModels)

---

## How to report findings

Always use this format in the final report, grouping by severity:

```
🔴 BLOCKER   — breaks build/feature, clear regression, security leak
🟠 MAJOR     — likely bug, untreated side-effect, violates a critical project convention
🟡 MINOR     — code smell, readability, duplication, missing edge-case test
🟢 NIT       — style preference, optional suggestion
```

Each finding must include:
- **file:line** (clickable link)
- **What is wrong** (one objective sentence)
- **Why it is a problem** (impact)
- **Suggested fix** (when obvious)

If nothing is found in a category, state explicitly "✅ No findings in {category}" — silence is not approval.

---

## Step 1 — Map the change surface

Run in parallel:

```bash
git status
git diff --stat <base-branch>...HEAD
git diff <base-branch>...HEAD
git log <base-branch>..HEAD --oneline
```

Build a mental list of:
- **New files** — need full review, not just the diff
- **Modified files** — focus on changed lines AND the surrounding context
- **Deleted files** — verify no remaining references
- **Public methods/classes added, removed, or renamed**
- **Signature changes** (added/removed parameters, type changes, visibility shifts)

> If the branch has many commits, also read `git log -p` of the last 5 to understand the evolution — something may have been added and then partially removed.

Before moving on, reconstruct a bottom-up behavior map for each changed flow:

```
entry point → state/data source → transformation → consumer/side effect → error/cleanup path
```

Do not compare this map to a proposed architecture yet.

---

## Step 2 — Trace call sites of everything touched

For **each public method/class** added, modified, or renamed, and each internal/private symbol whose
behavior crosses a file, module, lifecycle, serialization, persistence, navigation, or analytics
boundary:

```bash
# Where is it called?
grep -rn "methodName" --include="*.kt" --include="*.xml"

# Who implements/extends it? (for interfaces and open classes)
grep -rn ": InterfaceName" --include="*.kt"
grep -rn "override fun methodName" --include="*.kt"
```

For each call site found, ask:
- **Does the change break this caller?** (signature, behavior, contract)
- **Does the caller pass arguments in the new format?**
- **Is there a caller in a file NOT modified in this PR?** → potential 🔴 BLOCKER if the signature changed
- **Are there tests covering this caller?** If not, regression risk is exposed

> ⚠️ Watch for renames: if a method was renamed, every caller must have been updated. Confirm by grepping the **old name** — if it still appears, it is broken.

Do not stop at direct callers. Follow producers to consumers and writes to reads until the changed
behavior reaches an observable boundary or is proven dead.

---

## Step 3 — Audit side-effects and cross-module impact

Look for classic side effect categories, in order of risk:

### 3.1 Shared state
- Changes to `object`, `companion object`, singletons (Hilt `@Singleton`)
- Mutable variables in broad scope (`var` in shared classes)
- Mutating collections by reference

### 3.2 Threading & coroutines
- `Dispatchers` swapped (Main ↔ IO ↔ Default)
- `launch`/`async` without proper scope → leak
- `runBlocking` in production code → 🔴
- `MutableStateFlow`/`SharedFlow` with `replay`/`buffer` changed → alters semantics

### 3.3 Lifecycle & Compose
- `LaunchedEffect` with wrong `key` (`Unit` when it should have a dependency, or vice versa)
- `remember` without `key` when the value depends on a parameter → stale state
- `DisposableEffect` without `onDispose` → leak
- Expensive recompositions (inline lambda allocation, unstable lists)

### 3.4 Dependency Injection
- New `@Provides`/`@Binds` — may collide with existing binding
- Scope change (`@Singleton` → `@ActivityScoped`) → changes lifecycle of every consumer
- Removed binding → breaks every consumer

### 3.5 Persistence & network
- DAO/migration change → check there is a migration from the previous version
- DTO/Moshi adapter change → check backward compatibility
- SharedPreferences/DataStore key change → user data loss

### 3.6 Navigation & deep links
- Route added/removed → check callers of `navController.navigate(...)`
- Navigation argument change → check the destination's parser

---

## Step 4 — Validate project conventions

Load conventions the **project actually ships**: `00-capabilities.md`, `<repo>/.ai/project-profile.md` extra convention pointers, and repo `AGENTS.md`. Also load the kit's universal skills when the diff is relevant: `compose-android`,
`android-performance`, `ktlint-fixer`. For testing conventions use whichever **discovered** skill
`00-capabilities.md` lists — the repo owns its skills and this playbook must not name them.

Do **not** assume `.ai/rules/`, a design-system catalog, or SDUI exist. If they are listed, use them; if not, skip.

Record each source you loaded and what you checked. A listed source you skipped is a gap.

### Testing (the repo's discovered testing skill, if any, + the profile's test stack)
- [ ] New behavior / bugfix has a test
- [ ] Follow the module's existing mock/fake style
- [ ] Names describe behavior
- [ ] No `Thread.sleep`, no unnecessary `@VisibleForTesting`

### Lint (skill `ktlint-fixer` + profile engine)
- [ ] Profile **check** task passes on touched modules (or engine `none`)

---

## Step 5 — General quality audit

For every modified/new file, do a critical reading looking for:

### 5.1 Dead code & cruft
- Unused imports
- Variables declared and never read
- Private functions with no callers
- Newly added `TODO`/`FIXME` — must have an associated ticket
- Comments explaining **what** the code does (should explain **why**)
- A `Why:` comment that only restates reasoning already captured in the commit message / PR description — comments serve a future reader of the code, not a duplicate of delivery docs
- Debug logs (`Log.d`, `println`) left behind

### 5.2 Complexity
- Function > ~40 lines → extract
- Nesting > 3 levels → early return or extract
- More than 5 parameters → consider a data class
- An exhaustive `when` became partial (lost `else`/case) → 🟠

### 5.3 Duplication
- Identical block copied in 2+ places → extract
- Repeated magic constant → name it

### 5.4 Naming
- Variable name does not convey what it holds (`data`, `info`, `temp`)
- Imperative function named as a noun, or vice-versa
- Inconsistent acronyms (`URL` vs `Url`, `id` vs `Id`)

### 5.5 Error handling
- `try/catch` swallowing an exception with no log nor rethrow → 🟠
- `!!` on a value that may be null → 🟠
- `runCatching` without `.onFailure` → silent failure

### 5.6 Security & sensitive data
- Token/secret/PII in a log or exception message → 🔴
- User input concatenated into SQL/URL/HTML without sanitization → 🔴
- `WebView` with `javascriptEnabled = true` on untrusted content → 🔴

---

## Step 6 — Final checks before the verdict

Before closing the report, run (in parallel when possible):

```bash
./gradlew :{modified-modules}:lintKotlin
./gradlew :{modified-modules}:test
```

And confirm:
- [ ] No `.env`/credential/keystore file was added
- [ ] New strings in `strings.xml` have translation or are marked `translatable="false"`
- [ ] No unresolved merge conflicts (`<<<<<<<`, `=======`, `>>>>>>>`)
- [ ] Commit messages are meaningful (no "wip", "fix", "."`)
- [ ] Initial technical findings were drafted before reading design or implementation narratives
- [ ] Ticket/spec was read only afterward to check acceptance criteria and declared scope
- [ ] Implementation/test summaries were not used as proof; actual files were inspected

---

## Final checklist (the verdict)

- [ ] Step 1: change surface mapped (new / modified / deleted)
- [ ] Step 2: call sites of every changed public method traced
- [ ] Cross-boundary internal/private symbols traced end-to-end
- [ ] Step 3: side-effects audited (state, threading, DI, persistence, navigation)
- [ ] Step 4: project conventions validated against the sources actually loaded (each named in the report)
- [ ] Step 5: general quality reviewed (dead code, complexity, duplication, security)
- [ ] Step 6: lint + tests of modified modules ran locally
- [ ] Report grouped by severity (🔴 / 🟠 / 🟡 / 🟢) with file:line
- [ ] Explicit verdict: **READY FOR PR** or **FIX BEFORE OPENING PR**
- [ ] Initial findings were drafted before reading design/implementation narratives

> ⚠️ This skill **does not create commits nor open PRs automatically**. It only reports — the user decides what to fix.
