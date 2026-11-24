package com.notes.feature.notes

import com.notes.feature.notes.data.Note
import org.junit.Assert.assertEquals
import org.junit.Test

class NotesListViewModelTest {
    private val notes =
        listOf(
            Note(id = "a", title = "Groceries", body = "milk", editedAtMillis = 10),
            Note(id = "b", title = "Trip", body = "Pack the milk frother", editedAtMillis = 30),
            Note(id = "c", title = "Archived milk", body = "", archived = true, editedAtMillis = 50),
        )

    @Test
    fun `query matches title or body ignoring case`() {
        assertEquals(listOf("a"), NotesListViewModel.visibleNotes(notes, "GROC").map { it.id })
        assertEquals(listOf("b", "a"), NotesListViewModel.visibleNotes(notes, " milk ").map { it.id })
    }

    @Test
    fun `search also finds archived notes`() {
        assertEquals(listOf("a"), NotesListViewModel.visibleNotes(notes, "groceries").map { it.id })
    }

    @Test
    fun `no match gives an empty list`() {
        assertEquals(emptyList<Note>(), NotesListViewModel.visibleNotes(notes, "zzz"))
    }
}
