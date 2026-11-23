package com.notes.feature.notes

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Column
import androidx.compose.material3.IconButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag

@Composable
fun NotesListScreen(
    onNoteClick: (String) -> Unit,
    onArchiveClick: () -> Unit,
    onSettingsClick: () -> Unit,
) {
    Column(Modifier.testTag("notes_list")) {
        IconButton(onClick = onArchiveClick, modifier = Modifier.testTag("notes_archive_button")) { Text("Archive") }
        IconButton(onClick = onSettingsClick, modifier = Modifier.testTag("notes_settings_button")) { Text("Settings") }
        Text("First note", Modifier.testTag("note_row").clickable { onNoteClick("1") })
    }
}
