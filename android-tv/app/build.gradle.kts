import java.util.Properties
import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// The upload key signs release bundles for Play. Its location and passwords
// come from the environment (build.sh passes them) or keystore/keystore.properties
// (written by make-upload-key.sh); neither is in git. Without them the release
// bundle is left unsigned and build.sh refuses to call it a release.
val keystoreProps = Properties().apply {
    val file = rootProject.file("keystore/keystore.properties")
    if (file.exists()) file.inputStream().use { load(it) }
}

fun signingValue(env: String, prop: String): String? =
    System.getenv(env)?.takeIf { it.isNotBlank() } ?: keystoreProps.getProperty(prop)

// The player this app opens. Release builds always use production; a debug
// build can be pointed at a LAN player with -PplayerUrl=http://192.168.x.y:3001/
val playerUrl = (findProperty("playerUrl") as String?) ?: "https://play.khanshoof.com/"

android {
    namespace = "com.khanshoof.tv"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.khanshoof.tv"
        minSdk = 23
        targetSdk = 36
        // build.sh passes the git commit count, so every build Play sees is newer.
        versionCode = (findProperty("versionCode") as String?)?.toInt() ?: 1
        versionName = (findProperty("versionName") as String?) ?: "1.0.0"
        buildConfigField("String", "PLAYER_URL", "\"https://play.khanshoof.com/\"")
        manifestPlaceholders["usesCleartextTraffic"] = "false"
    }

    signingConfigs {
        create("upload") {
            signingValue("KHAN_UPLOAD_STORE_FILE", "storeFile")?.let { path ->
                storeFile = rootProject.file(path)
                storePassword = signingValue("KHAN_UPLOAD_STORE_PASSWORD", "storePassword")
                keyAlias = signingValue("KHAN_UPLOAD_KEY_ALIAS", "keyAlias")
                keyPassword = signingValue("KHAN_UPLOAD_KEY_PASSWORD", "keyPassword")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            signingConfig = signingConfigs.getByName("upload").takeIf { it.storeFile != null }
        }
        debug {
            // Installs next to the Play version, so a TV can have both.
            applicationIdSuffix = ".debug"
            versionNameSuffix = "-debug"
            buildConfigField("String", "PLAYER_URL", "\"$playerUrl\"")
            manifestPlaceholders["usesCleartextTraffic"] = "true"
        }
    }

    buildFeatures {
        buildConfig = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    lint {
        abortOnError = true
        checkReleaseBuilds = true
        // The SDK index check needs network and reports on Google's schedule,
        // not ours; it must not decide whether a build passes.
        disable += "GradleDependency"
        disable += "AndroidGradlePluginVersion"
        disable += "NewerVersionAvailable"
        // A TV banner is an xhdpi-only image by convention (320x180), and the
        // overlay face is in drawable-nodpi.
        disable += "IconMissingDensityFolder"
    }
}

kotlin {
    compilerOptions {
        jvmTarget.set(JvmTarget.JVM_17)
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.17.0")
    implementation("androidx.activity:activity-ktx:1.11.0")
    implementation("androidx.webkit:webkit:1.14.0")

    testImplementation("junit:junit:4.13.2")
}
