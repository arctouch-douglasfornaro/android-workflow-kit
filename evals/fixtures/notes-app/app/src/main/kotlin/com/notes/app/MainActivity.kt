package com.notes.app

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.testTagsAsResourceId
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.rememberNavController
import com.notes.feature.notes.notesGraph
import com.notes.feature.settings.SettingsActivity

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            val navController = rememberNavController()
            NavHost(
                navController = navController,
                startDestination = "notes",
                modifier = Modifier.semantics { testTagsAsResourceId = true },
            ) {
                notesGraph(
                    navController = navController,
                    onOpenNote = { id -> startActivity(NoteDetailActivity.intent(this@MainActivity, id)) },
                    onOpenSettings = { startActivity(Intent(this@MainActivity, SettingsActivity::class.java)) },
                )
            }
        }
    }
}
