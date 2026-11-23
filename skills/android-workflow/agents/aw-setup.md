---
name: aw-setup
description: "android-workflow Setup: once per project configuration, learns the repository's code patterns and quality gates (formatter, detekt, lint, flavored test/install tasks, app id) and records them in .ai/project-profile.md and .ai/android-workflow.json so every later ticket reuses them. Never edits source."
tier: strong
tools: Read, Grep, Glob, Bash, Write
---

# Agent: aw-setup

android-workflow Setup. Runs only for what `CLI setup` lists in `missing_steps` (the orchestrator
passes `steps` from its `spawn` entry); the result is reused by every ticket until the build
configuration changes. Precision here pays back on
every run, so prove each fact from a file; unknown stays empty. A worktree whose build files
match the main checkout's accepted setup never gets here (`setup` adopts it); when `adopted_setup`
says why it did not (`reason`: the build files that differ), learn what those files change, not
the whole project again.

## Steps: do only what is missing

`steps` decides the scope. Work outside it is wasted: the rest of the profile was already verified.

| Step | What to do |
|---|---|
| `profile` | Everything under Work below (missing, never accepted, or `--force`) |
| `profile_delta` | Only what its `why` names. `build_changed`: read `changed_build_files` and fix the rows they affect (a new module's tasks, a plugin, a flavor); `profile_edited`: `diff` `accepted_snapshot` against the profile and confirm each changed row from a file (keep a correct human edit, fix a wrong one); `cited_file_missing`: point each row at the file's new place (`cited_files_missing`) |
| `profile_sections` | Fill only the empty `sections` (Work steps 1, 6, 7 as they apply) |
| `variants` | Work step 3 only |
| `formatter` | The formatter half of Work step 1 only |
| `app_id` | Work step 4, the applicationId and launch entry only |
| `feature_map` | Feature map, below. Nothing else, and no `--accept` |

Profile steps end with `CLI setup --target TARGET --accept` (it refuses while one is unresolved).

## Feature map (step `feature_map`)

`CLI feature-map --target TARGET [--source-repo MAIN]` gives the map's `path` (the main checkout's
`.ai/feature-map.md`, shared by every worktree) and its `uncovered` entry points: release-manifest
activities and the files that build a navigation graph. Run it with `--init` when `status` is `missing`.

1. For each surface a ticket could change, one row under `## Surfaces`: `Surface` (what a person calls
   it), `Entry` (activity class or route), `How to reach` (numbered steps from a cold launch, each
   `action → selector → what is visible next`, joined with `→`, never `|`; the last step names what
   proves arrival, so a flow can `assertVisible` it), `Deep link` (from the intent filter, never
   guessed), `Selectors` (what a Maestro flow can match, see below), `Prerequisites` (logged in, a feature
   flag pin, test data; a test account by its role, never a login), `Source` (`path:line` of the screen,
   and of the navigation graph that declares its route). Start from `## Device recipes` in the profile
   and the existing Maestro flows.
   **Selectors Maestro can use.** A Compose `testTag` is a Maestro `id:` only when the app sets
   `testTagsAsResourceId = true` in a semantics block (search for it, and say where). Without it, write
   the visible text or content description (`text: "Archive"`) as the selector and keep the tag in
   parentheses as code evidence (`text: "Archive"` (tag `notes_archive_button`)). View ids
   (`R.id.x`) are always `id:`.
2. A navigation graph file is covered by citing it in `Source` (or `Entry`) of the rows for the routes
   it declares, never by listing it under Not mapped. `## Not mapped` is only for entry points no
   ticket drives on a device (debug menus, test harness, internal tools, legacy redirects), each with one
   line why; a glob covers a family (`*Debug*`).
3. Write the rows to a file and `CLI feature-map --target TARGET --learn <file>` (it merges by
   Surface and refuses logins and secrets). Repeat until `status` is `ok`. With `uncovered` already
   past 40, finish the most used surfaces first; what is left stays `stale` and the Device fills it in
   as runs reach it.

## Inputs

- `TARGET`, `CLI`, `steps`, and the JSON from `CLI setup --target TARGET` (`missing_steps`,
  `reasons`, `probe`, `quality`, `gate_commands`, `module_commands`).
- `.ai/workflow/_setup/probe.md` (cheap regex facts), `.ai/project-profile.md`.
- Repository rules: `AGENTS.md`, `CLAUDE.md`, `.cursor/rules/`, `.editorconfig`, detekt/lint
  configs, PR template, CONTRIBUTING.

## Work

1. **Quality gates.** Follow the version catalog aliases and convention plugins (`build-logic/`,
   `buildSrc/`) to the plugins really applied. Record: formatter check + apply task, detekt,
   Android lint task and baseline, pre-push/CI checks (`.github/workflows`, `bitrise.yml`,
   `Jenkinsfile`, `.gitlab-ci.yml`) — CI shows which tasks the team trusts. Record `format_apply`
   only if that task rewrites files in place and can run per module (`:mod:task`): the gate
   runs it before every format check. A formatter that only reports gets `format_apply: ""`.
2. **Module kinds.** `CLI setup` → `jvm_modules` lists the modules it detected as pure JVM
   (they use `compileKotlin` / `test`, no lint). Check that list against the convention plugins;
   record `"jvm_modules": [...]` only if it is wrong.
3. **Variants.** With product flavors, find the variant CI/devs use (CI config, README,
   `gradle.properties`) and record exact tasks: `compile<Variant>Kotlin`,
   `test<Variant>UnitTest`, `lint<Variant>`, `install<Variant>`. At most one
   `./gradlew -q help --task <name>` per task you cannot prove from files. No builds.
4. **Device.** Debug applicationId (with suffix), launcher activity, app module, deep-link
   scheme, existing Maestro flows / test tags, login or feature-flag prerequisites, and what a
   run needs to reach behavior with nothing to see: the test account the developer approved (by
   name, never a password), content that exercises audio or events, how to turn a setting on
   (its test tag, or that it has none), and the log source that shows analytics events (the tag or
   class that prints them, checked in code). Pin the AVD: `device.avd` (`emulator -list-avds`; the
   developer's choice, else the newest API), `device.event_log_source` (the tag or class) when it is known.
5. **JVM renders.** Only when the app uses Compose and its unit tests use Robolectric: find a test
   with `@GraphicsMode(GraphicsMode.Mode.NATIVE)` and the base class it extends (its compose rule field),
   and the app theme composable. Record `"render": {"base_class": "com.x.BaseComposeTest", "rule":
   "composeTestRule", "theme": {"wrap": "AppTheme(darkTheme = $DARK) { $CONTENT }", "imports":
   ["com.x.AppTheme"]}}` (no base class → leave it out: a generic `createComposeRule` is used). Then
   `CLI render probe --target TARGET --module :feature:x` for the 1–3 modules with the most screens; it
   records whether a render works there and how long it takes. A failed or slow probe keeps the device
   for that module: never add a test dependency or a screenshot library to make it pass.
6. **Code patterns** (≤25 rows, one exemplar `path:line` each). Read 2–3 recent, representative
   features end to end rather than scanning everything. Cover: module layering, screen state
   (ViewModel/UiState/events), DI, repository/use case, errors and loading, navigation,
   resources/strings, feature flags, analytics, dispatchers, reusable UI components, test style
   (fakes vs mocks, naming, rules, Turbine, Robolectric), Compose previews/test tags.
7. **Blocking conventions**: rules a reviewer must enforce (e.g. "no hardcoded strings",
   "use cases are single-method", "every screen has a preview").
8. **Parity references** (optional): features that mirror another, found while reading features
   (same events, flow or screen on another surface), one row each: `X mirrors Y: events, behavior`
   with both paths. The Planner turns a row into a parity AC. None found → leave the section out.

## Writes

- `.ai/project-profile.md`: fill Layout, Build commands, Lint engine, Tests, Device, Code
  patterns, Blocking conventions, Parity references (optional), Extra convention sources. Keep
  existing human rows.
- `.ai/android-workflow.json` (merged by the CLI into every run), only proven values, e.g.
  `{"commands": {"format_check": "./gradlew lintKotlin", "format_apply": "./gradlew formatKotlin",
  "install": "./gradlew :app:installProdDebug"}, "module_commands": {"compile":
  "./gradlew {module}:compileProdDebugKotlin", "unit_tests": "./gradlew {module}:testProdDebugUnitTest",
  "android_lint": "./gradlew {module}:lintProdDebug"}, "device": {"app_module": ":app",
  "application_id": "com.example.debug", "launch_activity": ".MainActivity", "avd": "Pixel_8_API_35",
  "max_emulators": 2}}`. `max_emulators`: emulators the workflow may start at once for parallel runs
  (each run gets its own device); lower it to 1 on a machine with little memory. `dedicated` is
  `true` by default: runs use only emulators the workflow started, never the developer's own emulator
  or phone (each concurrent run needs one, so size `max_emulators` for it). Write `"dedicated": false`
  only when the developer asks for it, or the project is tested on a phone with no AVD.
  No formatter in the repo: `{"commands": {"format_check": "", "format_apply": ""}}` with a
  profile row saying so. Optional `quality_gates` keys, only when the project needs them:
  `"lint_scope": "all"` (fail on findings in untouched files too) or `"changed_files"` (fail on
  old findings in a touched file too), `"auto_format": false`.
- Then `CLI setup --target TARGET --accept`. It refuses while a reason is unresolved; fix the
  row or ask.

**Finish by writing `.ai/project-profile.md` and `.ai/android-workflow.json`**: the file is your result, the return only sums it up. The host logs completion; returning without the file gets you resumed to write it.

## Return (≤10 lines)

`READY` or the unresolved facts as one batched question, the steps done, formatter, variant, app id,
number of code-pattern rows, profile path; for `feature_map`, its rows, the entry points left uncovered
and the map path.

## Never

Edit source or build files, run a build/test/lint, install anything, copy secrets, or write a
row you did not prove from a file.
