import com.android.build.api.dsl.ApplicationExtension
import org.gradle.api.Plugin
import org.gradle.api.Project
import org.gradle.kotlin.dsl.configure

class AndroidApplicationConventionPlugin : Plugin<Project> {
    override fun apply(target: Project) {
        with(target) {
            pluginManager.apply("com.android.application")
            pluginManager.apply("org.jetbrains.kotlin.android")
            pluginManager.apply("org.jmailen.kotlinter")
            extensions.configure<ApplicationExtension> {
                namespace = "com.notes.app"
                compileSdk = 35
                defaultConfig {
                    applicationId = "com.notes.app"
                    minSdk = 26
                    targetSdk = 35
                }
                buildTypes {
                    getByName("debug") {
                        applicationIdSuffix = ".debug"
                    }
                }
            }
        }
    }
}
