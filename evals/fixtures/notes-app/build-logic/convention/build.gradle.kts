plugins { `kotlin-dsl` }

dependencies {
    compileOnly("com.android.tools.build:gradle:8.13.0")
    compileOnly("org.jetbrains.kotlin:kotlin-gradle-plugin:2.2.21")
}

gradlePlugin {
    plugins {
        register("androidApplication") {
            id = "notes.android.application"
            implementationClass = "AndroidApplicationConventionPlugin"
        }
        register("androidFeature") {
            id = "notes.android.feature"
            implementationClass = "AndroidFeatureConventionPlugin"
        }
    }
}
