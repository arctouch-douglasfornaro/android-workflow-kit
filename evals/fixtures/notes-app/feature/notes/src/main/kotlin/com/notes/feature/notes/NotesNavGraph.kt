package com.notes.feature.notes

import androidx.compose.runtime.getValue
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.NavController
import androidx.navigation.NavGraphBuilder
import androidx.navigation.compose.composable
import com.notes.feature.notes.data.NotesRepository

fun NavGraphBuilder.notesGraph(
    navController: NavController,
    onOpenNote: (String) -> Unit,
    onOpenSettings: () -> Unit,
) {
    composable("notes") {
        val viewModel: NotesListViewModel = viewModel()
        val state by viewModel.state.collectAsStateWithLifecycle()
        NotesListScreen(
            state = state,
            onQueryChange = viewModel::onQueryChange,
            onNoteClick = onOpenNote,
            onArchiveClick = { navController.navigate("notes/archive") },
            onSettingsClick = onOpenSettings,
        )
    }
    composable("notes/archive") {
        val notes by NotesRepository.shared.observe().collectAsStateWithLifecycle()
        ArchiveScreen(notes = notes.filter { it.archived }, onNoteClick = onOpenNote)
    }
}
