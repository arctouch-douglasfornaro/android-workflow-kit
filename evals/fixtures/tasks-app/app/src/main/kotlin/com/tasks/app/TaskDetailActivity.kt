package com.tasks.app

import android.content.Context
import android.content.Intent
import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import com.tasks.app.data.TaskRepository
import com.tasks.app.databinding.ActivityTaskDetailBinding
import org.koin.android.ext.android.inject

class TaskDetailActivity : AppCompatActivity() {
    private val repository: TaskRepository by inject()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val binding = ActivityTaskDetailBinding.inflate(layoutInflater)
        setContentView(binding.root)
        // tasks://task/<id> or the extra set by intent()
        val id = intent.data?.lastPathSegment ?: intent.getStringExtra(EXTRA_ID).orEmpty()
        val task = repository.find(id)
        binding.taskTitle.text = task?.title ?: getString(R.string.task_not_found)
        binding.taskDone.isChecked = task?.done == true
        binding.taskDone.setOnCheckedChangeListener { _, _ -> repository.toggle(id) }
    }

    companion object {
        private const val EXTRA_ID = "task_id"

        fun intent(
            context: Context,
            id: String,
        ): Intent = Intent(context, TaskDetailActivity::class.java).putExtra(EXTRA_ID, id)
    }
}
