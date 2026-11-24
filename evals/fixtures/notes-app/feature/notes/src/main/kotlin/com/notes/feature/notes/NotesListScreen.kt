package com.notes.feature.notes

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.material3.IconButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextField
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag

@Composable
fun NotesListScreen(
    state: NotesListState,
    onQueryChange: (String) -> Unit,
    onNoteClick: (String) -> Unit,
    onArchiveClick: () -> Unit,
    onSettingsClick: () -> Unit,
) {
    Column(Modifier.testTag("notes_list")) {
        Row {
            IconButton(onClick = onArchiveClick, modifier = Modifier.testTag("notes_archive_button")) { Text("Archive") }
            IconButton(onClick = onSettingsClick, modifier = Modifier.testTag("notes_settings_button")) { Text("Settings") }
        }
        TextField(
            value = state.query,
            onValueChange = onQueryChange,
            placeholder = { Text("Search") },
            modifier = Modifier.testTag("notes_search"),
        )
        if (state.notes.isEmpty()) {
            Text("No notes", Modifier.testTag("notes_empty"))
        }
        state.notes.forEach { note ->
            Text(note.title, Modifier.testTag("note_row").clickable { onNoteClick(note.id) })
        }
    }
}
