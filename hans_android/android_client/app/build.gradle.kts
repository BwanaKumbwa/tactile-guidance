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
    // Declare .env as input (Gradle will track changes)
    inputs.file(envFile)
    
    // Output is a generated properties file
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
    compileSdk {
        version = release(36) {
            minorApiLevel = 1
        }
    }

    buildFeatures {
        buildConfig = true
    }

    defaultConfig {
        applicationId = "com.example.hans"
        minSdk = 24
        targetSdk = 36
        versionCode = 1
        versionName = "1.0"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"

        // Read directly from envMap (not local.properties)
        buildConfigField("String", "SERVER_IP", "\"${envMap["SERVER_IP"] ?: ""}\"")
        buildConfigField("String", "MAC_BRACELET", "\"${envMap["MAC_BRACELET"] ?: ""}\"")
        buildConfigField("String", "MAC_BELT", "\"${envMap["MAC_BELT"] ?: ""}\"")
        buildConfigField("String", "WAKE_WORD", "\"${envMap["WAKE_WORD"] ?: ""}\"")
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

// Hook envConfigTask into build lifecycle
tasks.named("preBuild") {
    dependsOn(envConfigTask)
}

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.appcompat)
    implementation(libs.material)
    implementation(libs.androidx.activity)
    implementation(libs.androidx.constraintlayout)
    testImplementation(libs.junit)
    androidTestImplementation(libs.androidx.junit)
    androidTestImplementation(libs.androidx.espresso.core)

    implementation("androidx.core:core-ktx:1.12.0")
    implementation("androidx.appcompat:appcompat:1.6.1")
    implementation("com.google.android.material:material:1.11.0")
    implementation("androidx.constraintlayout:constraintlayout:2.1.4")

    // --- CameraX (needed for BOTH flavors) ---
    val camerax_version = "1.3.1"
    implementation("androidx.camera:camera-core:${camerax_version}")
    implementation("androidx.camera:camera-camera2:${camerax_version}")
    implementation("androidx.camera:camera-lifecycle:${camerax_version}")
    implementation("androidx.camera:camera-view:${camerax_version}")

    // --- Networking (OkHttp) ---
    implementation("com.squareup.okhttp3:okhttp:4.12.0")

    // --- JSON ---
    implementation("org.json:json:20210307")

    // ===== Flavor-specific dependencies =====
    // ARCore only for withArcore flavor
    "withArcoreImplementation"("com.google.ar:core:1.41.0")
    
    // OpenGL for withArcore (needed for GLSurfaceView)
    "withArcoreImplementation"("androidx.core:core-ktx:1.12.0")
}