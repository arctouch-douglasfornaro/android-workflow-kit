package com.notes.feature.notes

import androidx.navigation.NavController
import androidx.navigation.NavGraphBuilder
import androidx.navigation.compose.composable

fun NavGraphBuilder.notesGraph(
    navController: NavController,
    onOpenNote: (String) -> Unit,
    onOpenSettings: () -> Unit,
) {
    composable("notes") {
        NotesListScreen(
            onNoteClick = onOpenNote,
            onArchiveClick = { navController.navigate("notes/archive") },
            onSettingsClick = onOpenSettings,
        )
    }
    composable("notes/archive") {
        ArchiveScreen(onNoteClick = onOpenNote)
    }
}
