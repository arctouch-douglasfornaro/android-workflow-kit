# Implementation notes: NOTES-22

## What changed
- `NotesListViewModel.archivedNotes(notes)`: archived notes sorted by `editedAtMillis` descending, next to `visibleNotes`.
- `NotesNavGraph`: the archive destination passes `archivedNotes(notes)` instead of an inline filter.

## Tests
- `archive lists only archived notes, newest edit first` (AC1): note d is newer than c but comes after it in the input, so the test fails without the sort.
- `archive is empty when nothing is archived` (AC2).

## Gate
`./gradlew lintKotlin :app:assembleDebug testDebugUnitTest` passes locally.
