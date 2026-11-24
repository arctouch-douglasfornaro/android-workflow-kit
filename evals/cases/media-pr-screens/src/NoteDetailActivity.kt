package com.notes.app

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.viewModels
import androidx.compose.foundation.layout.Column
import androidx.compose.material3.Button
import androidx.compose.material3.Text
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.testTagsAsResourceId
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import com.notes.feature.notes.NoteDetailViewModel
import com.notes.feature.notes.data.NotesRepository

class NoteDetailActivity : ComponentActivity() {
    private val noteId: String by lazy { intent.data?.lastPathSegment ?: intent.getStringExtra(EXTRA_ID).orEmpty() }

    private val viewModel: NoteDetailViewModel by viewModels { factory(noteId) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // notes://note/<id> or the extra set by intent()
        val note = NotesRepository.shared.find(noteId)
        setContent {
            val state by viewModel.state.collectAsStateWithLifecycle()
            LaunchedEffect(state.duplicatedId) {
                state.duplicatedId?.let { newId ->
                    startActivity(intent(this@NoteDetailActivity, newId))
                    finish()
                }
            }
            Column(Modifier.testTag("note_detail").semantics { testTagsAsResourceId = true }) {
                Text(text = note?.title ?: "Note not found", modifier = Modifier.testTag("note_detail_title"))
                Text(text = note?.body.orEmpty(), modifier = Modifier.testTag("note_detail_body"))
                if (note != null) {
                    Button(onClick = viewModel::duplicate, modifier = Modifier.testTag("note_detail_duplicate")) {
                        Text("Duplicate")
                    }
                }
                state.error?.let { Text(it, Modifier.testTag("note_detail_error")) }
            }
        }
    }

    companion object {
        private const val EXTRA_ID = "note_id"

        fun intent(
            context: Context,
            id: String,
        ): Intent = Intent(context, NoteDetailActivity::class.java).putExtra(EXTRA_ID, id)

        fun deepLink(id: String): Uri = Uri.parse("notes://note/$id")

        private fun factory(noteId: String): ViewModelProvider.Factory = viewModelFactory { initializer { NoteDetailViewModel(noteId) } }
    }
}
