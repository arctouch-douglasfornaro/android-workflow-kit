package com.tasks.app

import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import com.tasks.app.databinding.ActivitySettingsBinding

class SettingsActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(ActivitySettingsBinding.inflate(layoutInflater).root)
    }
}
