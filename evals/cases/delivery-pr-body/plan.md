# Plan: NOTES-22 Archive lists the most recently edited note first

Status: READY

## Objective
The Archive screen lists archived notes newest edit first, like the notes list.

## Acceptance criteria
| ID | Criterion | Proof |
|---|---|---|
| AC1 | Archive shows only archived notes, ordered by last edit, newest first | unit |
| AC2 | With nothing archived, Archive shows no rows | unit |

## Steps
1. `NotesListViewModel.archivedNotes(notes)` next to `visibleNotes` (pattern: `NotesListViewModel.kt:34`).
2. `NotesNavGraph` passes it to `ArchiveScreen`.
3. Unit tests in `NotesListViewModelTest`.

## Out of scope
- Search on the Archive screen.
- Restoring a note from the Archive screen (NOTES-23).

## Verification
- Unit: `./gradlew :feature:notes:testDebugUnitTest`.
