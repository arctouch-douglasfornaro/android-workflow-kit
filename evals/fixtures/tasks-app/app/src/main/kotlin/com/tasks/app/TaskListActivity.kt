package com.tasks.app

import android.content.Intent
import android.os.Bundle
import android.view.LayoutInflater
import android.view.ViewGroup
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.tasks.app.data.Task
import com.tasks.app.data.TaskRepository
import com.tasks.app.databinding.ActivityTaskListBinding
import org.koin.android.ext.android.inject

class TaskListActivity : AppCompatActivity() {
    private val repository: TaskRepository by inject()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val binding = ActivityTaskListBinding.inflate(layoutInflater)
        setContentView(binding.root)
        binding.taskList.layoutManager = LinearLayoutManager(this)
        binding.taskList.adapter =
            TaskAdapter(repository.sorted()) { task ->
                startActivity(TaskDetailActivity.intent(this, task.id))
            }
        binding.settingsButton.setOnClickListener {
            startActivity(Intent(this, SettingsActivity::class.java))
        }
    }
}

private class TaskAdapter(
    private val tasks: List<Task>,
    private val onClick: (Task) -> Unit,
) : RecyclerView.Adapter<TaskAdapter.Holder>() {
    class Holder(
        val view: TextView,
    ) : RecyclerView.ViewHolder(view)

    override fun onCreateViewHolder(
        parent: ViewGroup,
        viewType: Int,
    ): Holder = Holder(LayoutInflater.from(parent.context).inflate(R.layout.item_task, parent, false) as TextView)

    override fun onBindViewHolder(
        holder: Holder,
        position: Int,
    ) {
        val task = tasks[position]
        holder.view.text = if (task.done) "✓ ${task.title}" else task.title
        holder.view.setOnClickListener { onClick(task) }
    }

    override fun getItemCount(): Int = tasks.size
}
