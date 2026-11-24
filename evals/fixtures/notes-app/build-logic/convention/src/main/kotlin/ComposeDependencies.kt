import org.gradle.api.Project
import org.gradle.kotlin.dsl.dependencies

internal fun Project.composeDependencies() {
    dependencies {
        val bom = platform("androidx.compose:compose-bom:2025.10.01")
        add("implementation", bom)
        add("implementation", "androidx.compose.material3:material3")
        add("implementation", "androidx.compose.ui:ui")
        add("implementation", "androidx.activity:activity-compose:1.11.0")
        add("implementation", "androidx.navigation:navigation-compose:2.9.5")
        add("implementation", "androidx.lifecycle:lifecycle-viewmodel-compose:2.9.4")
        add("implementation", "androidx.lifecycle:lifecycle-runtime-compose:2.9.4")
        add("testImplementation", "junit:junit:4.13.2")
        add("testImplementation", "org.jetbrains.kotlinx:kotlinx-coroutines-test:1.10.2")
    }
}

internal fun Project.kotlinJvm17() {
    extensions.configure(org.jetbrains.kotlin.gradle.dsl.KotlinAndroidProjectExtension::class.java) {
        compilerOptions.jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17)
    }
}

internal fun Project.allowModulesWithoutTests() {
    tasks.withType(org.gradle.api.tasks.testing.Test::class.java).configureEach { failOnNoDiscoveredTests.set(false) }
}
