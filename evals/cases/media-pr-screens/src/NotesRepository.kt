package com.notes.feature.notes.data

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.withContext

/** In-memory store; the app has no backend. */
class NotesRepository(
    initial: List<Note> = SAMPLE,
    private val clock: () -> Long = System::currentTimeMillis,
) {
    private val notes = MutableStateFlow(initial)

    fun observe(): StateFlow<List<Note>> = notes.asStateFlow()

    fun find(id: String): Note? = notes.value.firstOrNull { it.id == id }

    fun setArchived(
        id: String,
        archived: Boolean,
    ) {
        notes.update { list -> list.map { if (it.id == id) it.copy(archived = archived) else it } }
    }

    /** Copies [id] as a new active note titled "<title> (copy)" and returns the new id. */
    suspend fun duplicate(id: String): String =
        withContext(Dispatchers.Default) {
            delay(SAVE_LATENCY_MS) // stands in for the disk write
            val source = requireNotNull(find(id)) { "no note $id" }
            val newId = (notes.value.maxOf { it.id.toInt() } + 1).toString()
            notes.update { it + source.copy(id = newId, title = "${source.title} (copy)", archived = false, editedAtMillis = clock()) }
            newId
        }

    companion object {
        private const val SAVE_LATENCY_MS = 300L

        /** Process-wide instance shared by the activities (no DI framework here). */
        val shared: NotesRepository by lazy { NotesRepository() }

        val SAMPLE =
            listOf(
                Note(id = "1", title = "First note", body = "Buy oat milk", editedAtMillis = 1_700_000_300_000),
                Note(id = "2", title = "Trip ideas", body = "Lisbon, Porto", editedAtMillis = 1_700_000_200_000),
                Note(id = "3", title = "Old recipe", body = "Bread", archived = true, editedAtMillis = 1_700_000_100_000),
            )
    }
}
