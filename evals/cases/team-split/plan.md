# Plan: NOTES-24 Archive order and a working dark theme switch

Status: READY

## Objective
Two small fixes from the same bug bash: the Archive lists newest edit first, and the Settings dark theme switch
actually toggles and remembers its value for the session.

## Acceptance criteria
| ID | Criterion | Proof |
|---|---|---|
| AC1 | Archive shows only archived notes, newest edit first | unit |
| AC2 | With nothing archived, Archive shows no rows | unit |
| AC3 | Tapping the dark theme switch turns it on, tapping again turns it off | unit |
| AC4 | Leaving Settings and coming back keeps the switch where the user left it (same process) | unit |

## Steps
1. `NotesListViewModel.archivedNotes(notes)` next to `visibleNotes` (`feature/notes/src/main/kotlin/com/notes/feature/notes/NotesListViewModel.kt:34`); `NotesNavGraph` passes it to `ArchiveScreen` (`feature/notes/src/main/kotlin/com/notes/feature/notes/NotesNavGraph.kt:27`). Tests in `NotesListViewModelTest`.
2. New `SettingsStore` (in-memory `MutableStateFlow<Boolean>` for dark theme, `shared` instance like `NotesRepository.shared`) and `SettingsViewModel` in `feature/settings`; `SettingsActivity` (`feature/settings/src/main/kotlin/com/notes/feature/settings/SettingsActivity.kt:17`) reads the state and calls `setDarkTheme`. Tests in a new `SettingsViewModelTest`.

## Parallel work
Two parts, no shared file:
- Part A (`:feature:notes`): `NotesListViewModel.kt`, `NotesNavGraph.kt`, `NotesListViewModelTest.kt`.
- Part B (`:feature:settings`): new `SettingsStore.kt`, new `SettingsViewModel.kt`, `SettingsActivity.kt`, new `SettingsViewModelTest.kt`.
The modules do not depend on each other.

## Out of scope
- Applying the dark theme to the app's colors.
- Persisting the switch across app restarts.

## Verification
- Unit: `./gradlew :feature:notes:testDebugUnitTest :feature:settings:testDebugUnitTest`.
