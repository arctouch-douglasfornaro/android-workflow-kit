plugins { `kotlin-dsl` }
gradlePlugin {
    plugins {
        register("androidApplication") {
            id = "notes.android.application"
            implementationClass = "AndroidApplicationConventionPlugin"
        }
    }
}
