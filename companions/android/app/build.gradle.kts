plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.jalora.companion"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.jalora.companion"
        minSdk = 26
        targetSdk = 35
        versionCode = 2
        versionName = "0.2.0"
    }

    dependencies {
        implementation("com.google.mlkit:text-recognition:16.0.1")
        testImplementation("junit:junit:4.13.2")
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }
}
