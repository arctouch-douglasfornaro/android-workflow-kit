plugins {
    id("notes.android.application")
}

dependencies {
    implementation(project(":feature:notes"))
    implementation(project(":feature:settings"))
}
