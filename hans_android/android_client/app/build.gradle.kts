plugins {
    alias(libs.plugins.android.application)
}

// Read .env for defaultConfig access
val envFile = rootDir.parentFile.resolve(".env")
val envMap = mutableMapOf<String, String>()

if (envFile.exists()) {
    envFile.readLines().forEach { line ->
        if (line.isNotBlank() && !line.startsWith("#")) {
            val parts = line.split("=", limit = 2)
            if (parts.size == 2) {
                envMap[parts[0].trim()] = parts[1].trim()
            }
        }
    }
    println("Loaded ${envMap.size} variables from .env")
} else {
    println(".env file not found at: ${envFile.absolutePath}")
}

// Create a task that tracks .env as input
val envConfigTask = tasks.register("generateEnvConfig") {
    inputs.file(envFile)

    val outputDir = layout.buildDirectory.dir("generated/env")
    val outputFile = outputDir.map { it.file("env.properties") }
    outputs.file(outputFile)

    doLast {
        val output = outputFile.get().asFile
        output.parentFile.mkdirs()
        output.writeText(envMap.entries.joinToString("\n") { "${it.key}=${it.value}" })
        println("Generated env config at ${output.absolutePath}")
    }
}

android {
    namespace = "com.example.hans"
    compileSdk = 36

    buildFeatures {
        buildConfig = true
    }

    // CRITICAL: Prevent Gradle from compressing .tflite files in assets,
    // which allows direct memory-mapping (mmap) without RAM duplication.
    androidResources {
        noCompress += listOf("tflite")
    }

    defaultConfig {
        applicationId = "com.example.hans"
        minSdk = 24
        targetSdk = 36
        versionCode = 1
        versionName = "1.0"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"

        buildConfigField("String", "SERVER_IP", "\"${envMap["SERVER_IP"] ?: ""}\"")
        buildConfigField("String", "MAC_BRACELET", "\"${envMap["MAC_BRACELET"] ?: ""}\"")
        buildConfigField("String", "MAC_BELT", "\"${envMap["MAC_BELT"] ?: ""}\"")
        buildConfigField("String", "WAKE_WORD", "\"${envMap["WAKE_WORD"] ?: ""}\"")

        buildConfigField("String", "API_URL", "\"${envMap["API_URL"] ?: ""}\"")
        buildConfigField("String", "API_KEY", "\"${envMap["API_KEY"] ?: ""}\"")
        buildConfigField("String", "LLM_MODEL", "\"${envMap["LLM_MODEL"] ?: "Qwen/Qwen3.5-122B-A10B-FP8"}\"")
    }

    // Flavor configuration
    flavorDimensions.add("arcore")

    productFlavors {
        create("withArcore") {
            dimension = "arcore"
            applicationIdSuffix = ".arcore"
            versionNameSuffix = "-arcore"
        }
        create("noArcore") {
            dimension = "arcore"
            applicationIdSuffix = ".noarcore"
            versionNameSuffix = "-noarcore"
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_11
        targetCompatibility = JavaVersion.VERSION_11
    }
}

tasks.named("preBuild") {
    dependsOn(envConfigTask)
}

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.appcompat)
    implementation(libs.material)
    implementation(libs.androidx.activity)
    implementation(libs.androidx.constraintlayout)
    implementation(libs.play.services.maps)
    testImplementation(libs.junit)
    androidTestImplementation(libs.androidx.junit)
    androidTestImplementation(libs.androidx.espresso.core)

    implementation("androidx.core:core-ktx:1.12.0")
    implementation("androidx.appcompat:appcompat:1.6.1")
    implementation("androidx.constraintlayout:constraintlayout:2.1.4")

    // --- CameraX ---
    val cameraxVersion = "1.3.1"
    implementation("androidx.camera:camera-core:$cameraxVersion")
    implementation("androidx.camera:camera-camera2:$cameraxVersion")
    implementation("androidx.camera:camera-lifecycle:$cameraxVersion")
    implementation("androidx.camera:camera-view:$cameraxVersion")

    // --- Google LiteRT (Modern TFLite runtime, 100% AGP 9.0+ compatible) ---
    val litertVersion = "1.0.1"
    implementation("com.google.ai.edge.litert:litert:$litertVersion")
    implementation("com.google.ai.edge.litert:litert-gpu:$litertVersion")
    implementation("com.google.ai.edge.litert:litert-api:$litertVersion")
    implementation("com.google.ai.edge.litert:litert-gpu-api:$litertVersion")

    // --- Networking (OkHttp) ---
    implementation("com.squareup.okhttp3:okhttp:4.12.0")

    // --- JSON ---
    implementation("org.json:json:20210307")

    // ===== Flavor-specific dependencies =====
    "withArcoreImplementation"("com.google.ar:core:1.41.0")
}
