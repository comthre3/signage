// Versions are pinned here and in app/build.gradle.kts; the toolchain that
// runs them (JDK, Gradle, SDK) is pinned in the Dockerfile.
plugins {
    id("com.android.application") version "8.13.2" apply false
    id("org.jetbrains.kotlin.android") version "2.2.21" apply false
}
