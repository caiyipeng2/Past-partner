import java.io.File

plugins {
    id("com.android.application")
    // The Flutter Gradle Plugin must be applied after the Android and Kotlin Gradle plugins.
    id("dev.flutter.flutter-gradle-plugin")
}

android {
    namespace = "com.pastpartner.past_partner"
    compileSdk = flutter.compileSdkVersion
    ndkVersion = flutter.ndkVersion

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    defaultConfig {
        // TODO: Specify your own unique Application ID (https://developer.android.com/studio/build/application-id.html).
        applicationId = "com.pastpartner.past_partner"
        // You can update the following values to match your application needs.
        // For more information, see: https://flutter.dev/to/review-gradle-config.
        minSdk = flutter.minSdkVersion
        targetSdk = flutter.targetSdkVersion
        versionCode = flutter.versionCode
        versionName = flutter.versionName
    }

    val storeRelease = providers.environmentVariable("PAST_PARTNER_ANDROID_STORE_RELEASE")
        .map { it.equals("true", ignoreCase = true) }
        .orElse(false)
        .get()
    val keystorePath = providers.environmentVariable("PAST_PARTNER_ANDROID_KEYSTORE_FILE").orNull
    val keystorePassword = providers.environmentVariable("PAST_PARTNER_ANDROID_KEYSTORE_PASSWORD").orNull
    val releaseKeyAlias = providers.environmentVariable("PAST_PARTNER_ANDROID_KEY_ALIAS").orNull
    val releaseKeyPassword = providers.environmentVariable("PAST_PARTNER_ANDROID_KEY_PASSWORD").orNull
    val missingSigningValues = buildList {
        if (keystorePath.isNullOrBlank() || !File(keystorePath).isFile) {
            add("PAST_PARTNER_ANDROID_KEYSTORE_FILE")
        }
        if (keystorePassword.isNullOrBlank()) {
            add("PAST_PARTNER_ANDROID_KEYSTORE_PASSWORD")
        }
        if (releaseKeyAlias.isNullOrBlank()) {
            add("PAST_PARTNER_ANDROID_KEY_ALIAS")
        }
        if (releaseKeyPassword.isNullOrBlank()) {
            add("PAST_PARTNER_ANDROID_KEY_PASSWORD")
        }
    }
    if (storeRelease && missingSigningValues.isNotEmpty()) {
        throw GradleException(
            "Android store-release signing configuration is incomplete: " +
                missingSigningValues.joinToString(", ") + "."
        )
    }
    val releaseSigningConfig = if (storeRelease && missingSigningValues.isEmpty()) {
        signingConfigs.create("release") {
            storeFile = File(keystorePath!!)
            storePassword = keystorePassword
            keyAlias = releaseKeyAlias
            keyPassword = releaseKeyPassword
        }
    } else {
        null
    }

    buildTypes {
        release {
            // Keep local release acceptance compatible; store mode is fail-closed above.
            signingConfig = releaseSigningConfig ?: signingConfigs.getByName("debug")
        }
    }
}

dependencies {
    // WorkManager provides OS-aware network constraints and bounded retry
    // scheduling; Dart remains responsible for authenticated upload work.
    implementation("androidx.work:work-runtime-ktx:2.9.1")
    implementation("androidx.core:core-ktx:1.13.1")
}

kotlin {
    compilerOptions {
        jvmTarget = org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17
    }
}

flutter {
    source = "../.."
}
