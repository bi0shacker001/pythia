plugins {
    kotlin("multiplatform") version "2.4.20"
    id("org.jetbrains.kotlin.plugin.compose") version "2.4.20"
    id("org.jetbrains.compose") version "1.13.0-beta01"
    id("dev.brahmkshatriya.compose") version "1.13.0-beta01"
    // The result types of the generated backend client (:core) are @Serializable; the version is the one :core applies.
    kotlin("plugin.serialization") version "2.4.20" apply false
}

// The patched build's version (CN_VERSION, settings.gradle.kts) or upstream's.
val composeNativeVersion = System.getenv("CN_VERSION") ?: "1.13.0-beta01"
val composeVersion = "1.13.0-beta01"
val uiLibVersion = "0.3.0" // mytop-ui's tag, which vendor/mytop-ui is pinned at; the composite build ignores it

kotlin {
    desktopNative {
        binaries.executable {
            entryPoint = "dev.mytop.pythia.ui.main"
        }
    }

    sourceSets {
        commonMain {
            dependencies {
                implementation("org.jetbrains.compose.ui:ui:$composeVersion")
                implementation("org.jetbrains.compose.foundation:foundation:$composeVersion")
                implementation("org.jetbrains.compose.material3:material3:$composeVersion")
                implementation(project(":core"))
                implementation("dev.droidtop.lib.ui:tokens:$uiLibVersion")
                implementation("dev.droidtop.lib.ui:format:$uiLibVersion")
            }
        }
        named("desktopNativeMain") {
            dependencies {
                implementation("dev.brahmkshatriya.compose.ui:ui:$composeNativeVersion")
                implementation("dev.brahmkshatriya.compose.foundation:foundation:$composeNativeVersion")
                implementation("dev.brahmkshatriya.compose.material3:material3:$composeNativeVersion")
                implementation("dev.brahmkshatriya.compose.desktop:desktop-native:$composeNativeVersion")
            }
        }
    }
}

composeNativeApplication {
    applicationName.set("Pythia")
    packageName.set("dev.mytop.pythia.ui")
    executableName.set("pythia-ui")
}
