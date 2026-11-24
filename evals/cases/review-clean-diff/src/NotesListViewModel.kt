package com.notes.feature.notes

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.notes.feature.notes.data.Note
import com.notes.feature.notes.data.NotesRepository
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.stateIn

data class NotesListState(
    val query: String = "",
    val notes: List<Note> = emptyList(),
)

class NotesListViewModel(
    private val repository: NotesRepository = NotesRepository.shared,
) : ViewModel() {
    private val query = MutableStateFlow("")

    val state: StateFlow<NotesListState> =
        combine(repository.observe(), query) { notes, q -> NotesListState(q, visibleNotes(notes, q)) }
            .stateIn(viewModelScope, SharingStarted.Eagerly, NotesListState())

    fun onQueryChange(value: String) {
        query.value = value
    }

    fun archive(id: String) = repository.setArchived(id, archived = true)

    companion object {
        /** Archived notes, most recently edited first. */
        fun archivedNotes(notes: List<Note>): List<Note> = notes.filter { it.archived }.sortedByDescending { it.editedAtMillis }

        /** Active notes matching [query] in the title or body, most recently edited first. */
        fun visibleNotes(
            notes: List<Note>,
            query: String,
        ): List<Note> {
            val q = query.trim()
            return notes
                .filter { !it.archived }
                .filter { q.isEmpty() || it.title.contains(q, ignoreCase = true) || it.body.contains(q, ignoreCase = true) }
                .sortedByDescending { it.editedAtMillis }
        }
    }
}
