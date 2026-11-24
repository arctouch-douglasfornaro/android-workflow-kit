# Feature map

Every surface a device run may need and how to reach it from a cold launch. Read by the Planner
and the Device; written by Setup and `CLI feature-map --learn`. No credentials or user data:
name a test account by its role, never its login.

## Surfaces

| Surface | Entry | How to reach | Deep link | Selectors | Prerequisites | Source |
|---|---|---|---|---|---|---|
| Notes list | MainActivity, route `notes` | 1. Cold launch → `notes_list` visible | | `notes_list`, `notes_search`, `note_row`, `notes_empty` | none | `feature/notes/src/main/kotlin/com/notes/feature/notes/NotesListScreen.kt:21` |
| Archive | route `notes/archive` | 1. Cold launch → `notes_list` 2. tap `notes_archive_button` → `archive_list` visible | | `archive_list`, `archive_row` | none | `feature/notes/src/main/kotlin/com/notes/feature/notes/NotesNavGraph.kt:27` |
| Note detail | NoteDetailActivity | 1. Cold launch → `notes_list` 2. tap the first `note_row` → `note_detail` visible | `notes://note/1` (`adb shell am start -a android.intent.action.VIEW -d notes://note/1`) | `note_detail`, `note_detail_title`, `note_detail_body` | the note with id 1 (the first row on a fresh install) | `app/src/main/kotlin/com/notes/app/NoteDetailActivity.kt:17` |
| Settings | SettingsActivity | 1. Cold launch → `notes_list` 2. tap `notes_settings_button` → `settings_screen` visible | | `settings_screen`, `settings_dark_theme` | none | `feature/settings/src/main/kotlin/com/notes/feature/settings/SettingsActivity.kt:17` |
| Main host | MainActivity | Cold launch | | `notes_list` | none | `app/src/main/kotlin/com/notes/app/MainActivity.kt:18` |

## Not mapped

Entry points left out on purpose (debug, test, internal). Globs work: `*Debug*`, `feature/internal/**`.

| Entry | Why |
|---|---|
| ComponentGalleryActivity | internal design catalog, not reachable from the app |
