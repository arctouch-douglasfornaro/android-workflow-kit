package com.notes.feature.notes.data

import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test

class NotesRepositoryTest {
    @Test
    fun `duplicate adds an active copy with a new id`() =
        runTest {
            val repository = NotesRepository(clock = { 42L })

            val newId = repository.duplicate("3")

            val copy = repository.find(newId)!!
            assertEquals("4", newId)
            assertEquals("Old recipe (copy)", copy.title)
            assertEquals("Bread", copy.body)
            assertFalse(copy.archived)
            assertEquals(42L, copy.editedAtMillis)
        }
}
