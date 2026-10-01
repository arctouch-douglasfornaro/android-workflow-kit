# Project profile

Local, persistent facts; repository code and rules win over stale rows.
Created automatically by `feature_setup.py`; completed by `project-explorer`.
Unknown cells stay empty. Copy only useful sections; no placeholders or credentials.

## Provenance
| What | Value |
|---|---|
| Configuration tracking | `.ai/workflow/_setup/profile-state.json` |
| Fact sources | |

## Layout
| What | Value |
|---|---|
| Android Gradle project | |
| Settings / wrapper | |
| App module(s) | |
| UI toolkit / navigation | |
| DI | |

## Build commands
Resolve convention plugins and version aliases before classifying a module.
Record proven tasks/variants; do not classify every nonliteral Android declaration as JVM.

| Module / pattern | Plugin / variant | Compile | Unit tests | Lint | Evidence |
|---|---|---|---|---|---|

| What | Value |
|---|---|
| Recommended safe flags | |
| Downstream consumers at risk | |
| APK build / install tasks | |

## Lint engine
| What | Value |
|---|---|
| Engine / check / format | |
| Not auto-fixable | |
| Pre-push hook requirements | |

## Tests
| What | Value |
|---|---|
| Runner / mocking / async | |
| Existing screenshot engine / task | |
| Reusable device flows | |
| Test convention sources | |

## Device
| What | Value |
|---|---|
| Debug application ID / launcher | |
| Existing AVD authorized for automatic start | |
| Entry point / deep link | |
| Observable event/state handle | |
| Known runtime prerequisites | |

## Environment repairs
| Verified symptom | Authorized non-destructive repair |
|---|---|

## Feature overview docs
| What | Value |
|---|---|
| Recognized glob (none if absent) | |
| Validator | |

## Code patterns
How this repository already solves recurring problems. One row per area, each with an
exemplar the Planner can name and the Reviewer can hold the diff against. ≤25 rows.

| Area | Pattern used here | Exemplar (path:line) |
|---|---|---|

Typical areas: module layering, screen state (ViewModel/UiState/events), DI wiring,
repository/use case, error and loading handling, navigation/deep links, strings/resources,
feature flags, analytics, coroutine dispatchers, test style (fakes vs mocks, naming, rules),
Compose previews/test tags, reusable UI components.

## Project conventions that are blocking, not stylistic
| Rule | Evidence / diff-scoped command |
|---|---|

## Test data
Only approved fixture identifiers; no secrets or invented accounts.

## Extra convention sources
Pointers to repository `AGENTS.md` and applicable project skills, not copied content.
