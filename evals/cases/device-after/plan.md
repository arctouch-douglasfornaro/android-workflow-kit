# Plan: NOTES-12 Show when a note was last edited on the note detail screen

Status: READY

## Objective
Note detail shows "Edited on <date>" under the title so people can tell whether they see the latest version.

## Acceptance criteria
| ID | Criterion | Proof |
|---|---|---|
| AC1 | Opening note 1 shows "Edited on Nov 14, 2023" under the title (tag `note_detail_edited`) | device |
| AC2 | Opening an unknown note shows "Note not found" and no edited line | device |

## Steps
1. `NoteDetailActivity`: a `Text` with tag `note_detail_edited` between title and body, only for an existing note; `editedDate(millis)` formats the day in UTC (`app/src/main/kotlin/com/notes/app/NoteDetailActivity.kt:17`).

## Out of scope
- Relative times ("5 minutes ago").

## Verification
Device recipe (feature map row "Note detail"):
1. `adb shell am start -a android.intent.action.VIEW -d notes://note/1 com.notes.app.debug` → `note_detail` visible.
2. Assert `note_detail_edited` text "Edited on Nov 14, 2023". Capture `note-detail-edited`.
3. `adb shell am start -a android.intent.action.VIEW -d notes://note/999 com.notes.app.debug` → `note_detail_title` "Note not found", no `note_detail_edited`. Capture `note-detail-not-found`.
