package com.notes.feature.notes.data

data class Note(
    val id: String,
    val title: String,
    val body: String,
    val archived: Boolean = false,
    val editedAtMillis: Long,
)
