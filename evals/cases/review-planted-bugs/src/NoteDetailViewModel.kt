package com.notes.feature.notes

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.notes.feature.notes.data.NotesRepository
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

data class NoteDetailState(
    val duplicatedId: String? = null,
    val error: String? = null,
)

class NoteDetailViewModel(
    private val noteId: String,
    private val repository: NotesRepository = NotesRepository.shared,
) : ViewModel() {
    private val _state = MutableStateFlow(NoteDetailState())
    val state: StateFlow<NoteDetailState> = _state.asStateFlow()

    fun duplicate() {
        viewModelScope.launch {
            runCatching { repository.duplicate(noteId) }
                .onSuccess { newId -> _state.value = NoteDetailState(duplicatedId = newId) }
                .onFailure { _state.value = NoteDetailState(error = "Couldn't duplicate the note") }
        }
    }
}
