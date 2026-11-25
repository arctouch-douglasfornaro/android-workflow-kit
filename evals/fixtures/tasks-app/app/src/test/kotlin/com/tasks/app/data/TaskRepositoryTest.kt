package com.tasks.app.data

import org.junit.Assert.assertEquals
import org.junit.Test

class TaskRepositoryTest {
    @Test
    fun `open tasks come before done ones`() {
        assertEquals(listOf("1", "3", "2"), TaskRepository().sorted().map { it.id })
    }

    @Test
    fun `toggle flips done`() {
        val repository = TaskRepository()
        repository.toggle("1")
        assertEquals(true, repository.find("1")?.done)
    }
}
