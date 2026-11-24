package com.notes.feature.notes

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Column
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import com.notes.feature.notes.data.Note

@Composable
fun ArchiveScreen(
    notes: List<Note>,
    onNoteClick: (String) -> Unit,
) {
    Column(Modifier.testTag("archive_list")) {
        Text("Archived notes")
        notes.forEach { note ->
            Text(note.title, Modifier.testTag("archive_row").clickable { onNoteClick(note.id) })
        }
    }
}
