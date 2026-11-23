rootProject.name = "pythia-ui"

pluginManagement {
    repositories {
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositories {
        mavenCentral()
        google()
        // Official Skiko 0.156.1 for the JVM/iOS/web closure comes from JetBrains' dev repository
        // (compose-native PUBLISHING.md); the native fork of Skiko is on Maven Central.
        maven("https://packages.jetbrains.team/maven/p/cmp/dev") {
            content { includeGroup("org.jetbrains.skiko") }
        }
    }
}

// The shared UI library (tokens, format, the backend contract) is consumed from source, at the tag the submodule is
// pinned to, so nothing of it is copied here (Droidtop/tracker#958).
includeBuild("vendor/mytop-ui") {
    dependencySubstitution {
        for (name in listOf("contract", "tokens", "format", "notices")) {
            substitute(module("dev.droidtop.lib.ui:$name")).using(project(":$name"))
        }
    }
}
