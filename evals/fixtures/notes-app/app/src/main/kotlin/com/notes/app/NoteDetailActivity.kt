package com.notes.app

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Column
import androidx.compose.material3.Text
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag

class NoteDetailActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // notes://note/<id> or the extra set by intent()
        val id = intent.data?.lastPathSegment ?: intent.getStringExtra(EXTRA_ID).orEmpty()
        setContent {
            Column(Modifier.testTag("note_detail")) {
                Text(text = "Note $id", modifier = Modifier.testTag("note_detail_title"))
                Text(text = "", modifier = Modifier.testTag("note_detail_body"))
            }
        }
    }

    companion object {
        private const val EXTRA_ID = "note_id"

        fun intent(context: Context, id: String): Intent =
            Intent(context, NoteDetailActivity::class.java).putExtra(EXTRA_ID, id)

        fun deepLink(id: String): Uri = Uri.parse("notes://note/$id")
    }
}
