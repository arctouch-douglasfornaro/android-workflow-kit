# Implementation notes: NOTES-12

## What changed
- `NoteDetailActivity`: "Edited on <date>" under the title (tag `note_detail_edited`) for an existing note; `editedDate` formats the day as `MMM d, yyyy` in UTC.

## Tests
- AC1 and AC2 are device AC (the screen is an Activity with no Compose UI test setup in this repo).
