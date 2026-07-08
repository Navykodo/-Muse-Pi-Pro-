import java.util.Properties

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.compose)
}

val localProperties = Properties().apply {
    val file = rootProject.file("local.properties")
    if (file.isFile) {
        file.inputStream().use(::load)
    }
}

fun configValue(name: String, fallback: String = ""): String {
    return providers.gradleProperty(name).orNull
        ?: providers.environmentVariable(name).orNull
        ?: localProperties.getProperty(name)
        ?: fallback
}

fun configString(name: String): String {
    val value = configValue(name)
        .replace("\\", "\\\\")
        .replace("\"", "\\\"")
    return "\"$value\""
}

fun configInt(name: String): Int = configValue(name, "0").toIntOrNull() ?: 0

fun configLong(name: String, fallback: String): Long = configValue(name, fallback).toLongOrNull()
    ?: fallback.toLong()

android {
    namespace = "com.example.hello_world"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.example.hello_world"
        minSdk = 24
        targetSdk = 36
        versionCode = 1
        versionName = "1.0"

        buildConfigField("String", "HARDWARE_BOARD_HOST", configString("HARDWARE_BOARD_HOST"))
        buildConfigField("int", "HARDWARE_API_PORT", configInt("HARDWARE_API_PORT").toString())
        buildConfigField("int", "HARDWARE_CAR_PORT", configInt("HARDWARE_CAR_PORT").toString())
        buildConfigField("int", "HARDWARE_ALERT_PORT", configInt("HARDWARE_ALERT_PORT").toString())
        buildConfigField("String", "HARDWARE_MJPEG_URL", configString("HARDWARE_MJPEG_URL"))
        buildConfigField("String", "HARDWARE_CAMERA_USER", configString("HARDWARE_CAMERA_USER"))
        buildConfigField("String", "HARDWARE_CAMERA_PASS", configString("HARDWARE_CAMERA_PASS"))
        buildConfigField("String", "HARDWARE_BROWSER_URL", configString("HARDWARE_BROWSER_URL"))
        buildConfigField("long", "HARDWARE_SEND_MS", "${configLong("HARDWARE_SEND_MS", "50")}L")
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_11
        targetCompatibility = JavaVersion.VERSION_11
    }
    buildFeatures {
        buildConfig = true
        compose = true
    }
}

dependencies {
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.compose.material3)
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.ui.graphics)
    implementation(libs.androidx.compose.ui.tooling.preview)
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.lifecycle.runtime.ktx)
    implementation(libs.ktor.client.core)
    implementation(libs.ktor.client.cio)
    debugImplementation(libs.androidx.compose.ui.tooling)
}
