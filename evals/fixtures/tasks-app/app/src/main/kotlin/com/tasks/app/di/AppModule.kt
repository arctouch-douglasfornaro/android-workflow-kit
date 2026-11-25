package com.tasks.app.di

import com.tasks.app.data.TaskRepository
import org.koin.dsl.module

val appModule =
    module {
        single { TaskRepository() }
    }
