# Plan: NOTES-21 Duplicate a note from the note detail screen

Status: READY

## Objective
A "Duplicate" button on the note detail screen copies the note and opens the copy.

## Acceptance criteria
| ID | Criterion | Proof |
|---|---|---|
| AC1 | Note detail shows a "Duplicate" button (tag `note_detail_duplicate`) for an existing note, none for "Note not found" | device |
| AC2 | Tapping it creates one active copy titled "<title> (copy)" with the same body and a new id, edited now | unit |
| AC3 | After the copy is saved, the screen closes and the copy's detail screen opens | device |
| AC4 | If saving fails, the screen stays and shows "Couldn't duplicate the note" (tag `note_detail_error`) | unit |

## Steps
1. `NotesRepository.duplicate(id)`: suspend, simulated disk write, returns the new id (`feature/notes/.../data/NotesRepository.kt`).
2. `NoteDetailViewModel` with `duplicate()` and a `StateFlow<NoteDetailState>` (pattern: `NotesListViewModel.kt:18`).
3. `NoteDetailActivity`: the button, the error text, and opening the copy (`app/src/main/kotlin/com/notes/app/NoteDetailActivity.kt:17`).
4. Unit test for the repository copy.

## Out of scope
- Duplicating from the notes list.
- Undo.

## Verification
- Unit: `./gradlew :feature:notes:testDebugUnitTest`.
- Device: open `notes://note/1`, tap `note_detail_duplicate`, the detail of "First note (copy)" opens. Capture `note-duplicated`.
