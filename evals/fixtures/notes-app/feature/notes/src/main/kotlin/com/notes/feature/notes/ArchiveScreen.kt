package com.notes.feature.notes

import androidx.compose.foundation.layout.Column
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag

@Composable
fun ArchiveScreen(onNoteClick: (String) -> Unit) {
    Column(Modifier.testTag("archive_list")) {
        Text("Archived notes")
    }
}
