# Implementation notes: NOTES-21

## What changed
- `NotesRepository.duplicate(id)` copies a note on `Dispatchers.Default` after a 300 ms simulated write and returns the new id (max id + 1). The repository takes a `clock` so the test can pin the edit time.
- New `NoteDetailViewModel` (`feature/notes`) runs the copy in `viewModelScope` and exposes `NoteDetailState(duplicatedId, error)`; a failure sets the error text (AC4).
- `NoteDetailActivity` gets the ViewModel through a `viewModelFactory`, shows the Duplicate button only for an existing note (AC1), the error text, and on `duplicatedId` opens the copy and finishes (AC3).

## Tests
- `NotesRepositoryTest.duplicate adds an active copy with a new id` (AC2).
- AC1 and AC3 are device AC.

## Gate
`./gradlew lintKotlin :app:assembleDebug testDebugUnitTest` passes locally.

## Risks
None known. The button is safe to tap more than once: the copy finishes the screen.
