import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}
val local = Properties().apply {
    val file = rootProject.file("local.properties")
    if (file.exists()) file.inputStream().use { load(it) }
}
fun config(name: String): String = "\"" + (local.getProperty(name) ?: "").replace("\\", "\\\\").replace("\"", "\\\"") + "\""
android {
    namespace = "com.example.entrenamiento"
    compileSdk = 35
    defaultConfig {
        applicationId = "com.example.entrenamiento"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "0.1"
        buildConfigField("String", "API_URL", config("API_URL"))
        buildConfigField("String", "SUPABASE_URL", config("SUPABASE_URL"))
        buildConfigField("String", "SUPABASE_ANON_KEY", config("SUPABASE_ANON_KEY"))
    }
    buildFeatures { compose = true; buildConfig = true }
    compileOptions { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
    kotlinOptions { jvmTarget = "17" }
}
dependencies {
    implementation(platform("androidx.compose:compose-bom:2024.12.01"))
    implementation("androidx.activity:activity-compose:1.10.0")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.foundation:foundation")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.8.7")
}
