package com.tasks.app.data

/** In-memory tasks; the app has no backend. Provided as a Koin singleton. */
class TaskRepository {
    private val tasks =
        mutableListOf(
            Task(id = "1", title = "Water the plants"),
            Task(id = "2", title = "Call the bank", done = true),
            Task(id = "3", title = "Book the dentist"),
        )

    fun all(): List<Task> = tasks.toList()

    fun find(id: String): Task? = tasks.firstOrNull { it.id == id }

    fun toggle(id: String) {
        val index = tasks.indexOfFirst { it.id == id }
        if (index >= 0) tasks[index] = tasks[index].copy(done = !tasks[index].done)
    }

    /** Open tasks first, then done ones, each group in insertion order. */
    fun sorted(): List<Task> = tasks.sortedBy { it.done }
}
