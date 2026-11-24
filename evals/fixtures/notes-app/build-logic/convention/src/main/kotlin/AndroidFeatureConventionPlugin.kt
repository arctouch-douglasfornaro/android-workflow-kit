import com.android.build.api.dsl.LibraryExtension
import org.gradle.api.Plugin
import org.gradle.api.Project
import org.gradle.kotlin.dsl.configure

class AndroidFeatureConventionPlugin : Plugin<Project> {
    override fun apply(target: Project) {
        with(target) {
            pluginManager.apply("com.android.library")
            pluginManager.apply("org.jetbrains.kotlin.android")
            pluginManager.apply("org.jetbrains.kotlin.plugin.compose")
            pluginManager.apply("org.jmailen.kotlinter")
            extensions.configure<LibraryExtension> {
                compileSdk = 36
                defaultConfig { minSdk = 26 }
                buildFeatures { compose = true }
                compileOptions {
                    sourceCompatibility = org.gradle.api.JavaVersion.VERSION_17
                    targetCompatibility = org.gradle.api.JavaVersion.VERSION_17
                }
                testOptions { unitTests.isIncludeAndroidResources = true }
            }
            kotlinJvm17()
            allowModulesWithoutTests()
            composeDependencies()
        }
    }
}
