import groovy.json.JsonSlurper
import org.jetbrains.kotlin.gradle.plugin.mpp.KotlinNativeTarget

plugins {
    kotlin("multiplatform")
    kotlin("plugin.serialization")
}

val uiLibVersion = "0.5.0" // source pinned in vendor/mytop-ui

/**
 * Writes the Kotlin client of Pythia's backend from schema/backend-ipc.json (pythia.ipc.schema writes that file from
 * the Backend's own slots and signals and the result types of pythia/ipc/results.py). Nothing generated is checked in;
 * the build makes it, so the client cannot drift from the schema:
 *
 *  - a `@Serializable` data class for every entry of `types` (a key marked optional, or that may be null, is nullable
 *    with a default; keys the schema does not name are ignored when an answer is read),
 *  - a `PythiaBackend` with one suspend function per method: a method with a `result` returns `TypedAnswer<Type>`
 *    (the answer's fields read as that type), any other returns the backend contract's `Answer` as it came,
 *  - one Flow per event: the payload type for an event with a `payload`, else the event's arguments,
 *  - for the tests, one full and one sparse sample answer per typed method and payload per typed event, made from the
 *    same types, with a key the schema does not know in each.
 */
abstract class GenerateBackendClient : DefaultTask() {
    @get:InputFile
    abstract val schema: RegularFileProperty

    @get:OutputDirectory
    abstract val outputDir: DirectoryProperty

    @get:OutputDirectory
    abstract val testOutputDir: DirectoryProperty

    private val keywords = setOf(
        "as", "break", "class", "continue", "do", "else", "false", "for", "fun", "if", "in", "interface", "is", "null",
        "object", "package", "return", "super", "this", "throw", "true", "try", "typealias", "typeof", "val", "var",
        "when", "while",
    )

    private fun camel(name: String): String {
        val parts = name.split('_').filter { it.isNotEmpty() }
        val joined = parts.first() + parts.drop(1).joinToString("") { it.replaceFirstChar { c -> c.uppercase() } }
        return if (joined in keywords) "`$joined`" else joined
    }

    private fun kotlinType(type: String): String = when (type) {
        "string" -> "String"
        "boolean" -> "Boolean"
        "integer" -> "Long"
        "number" -> "Double"
        "any" -> "JsonElement"
        else -> throw GradleException("The schema has a parameter type '$type' the generator does not know.")
    }

    /** The Kotlin type of a schema type: a name, or {enum}, {oneOf}, {array}, {map} or {object}. */
    private fun typeOf(type: Any?): String = when (type) {
        "string" -> "String"
        "boolean" -> "Boolean"
        "integer" -> "Long"
        "number" -> "Double"
        "any", "null" -> "JsonElement?"
        is Map<*, *> -> when {
            type.containsKey("enum") -> "String"
            type.containsKey("array") -> "List<${typeOf(type["array"])}>"
            type.containsKey("map") -> "Map<String, ${typeOf(type["map"])}>"
            type.containsKey("object") -> type["object"] as String
            type.containsKey("oneOf") -> {
                val members = type["oneOf"] as List<*>
                val present = members.filter { it != "null" }
                val inner = if (present.size == 1) typeOf(present[0]) else "JsonElement"
                if (members.contains("null") && !inner.endsWith("?")) "$inner?" else inner
            }
            else -> throw GradleException("The schema has a type the generator does not know: $type")
        }
        else -> throw GradleException("The schema has a type the generator does not know: $type")
    }

    /** One JSON text that is a valid value of the schema type. `full` fills every optional key and every list. */
    private fun sample(type: Any?, types: Map<String, Any?>, full: Boolean): String = when (type) {
        "string" -> "\"sample\""
        "boolean" -> "true"
        "integer" -> "7"
        "number" -> "1.5"
        "any" -> if (full) "{\"k\":1}" else "null"
        "null" -> "null"
        is Map<*, *> -> when {
            type.containsKey("enum") -> "\"" + (type["enum"] as List<*>).first() + "\""
            type.containsKey("array") -> if (full) "[" + sample(type["array"], types, full) + "]" else "[]"
            type.containsKey("map") -> if (full) "{\"a\":" + sample(type["map"], types, full) + "}" else "{}"
            type.containsKey("object") -> objectSample(type["object"] as String, types, full)
            type.containsKey("oneOf") -> {
                val members = type["oneOf"] as List<*>
                if (!full && members.contains("null")) "null" else sample(members.first { it != "null" }, types, full)
            }
            else -> throw GradleException("The schema has a type the generator does not know: $type")
        }
        else -> throw GradleException("The schema has a type the generator does not know: $type")
    }

    private fun objectSample(name: String, types: Map<String, Any?>, full: Boolean): String {
        @Suppress("UNCHECKED_CAST")
        val fields = (types[name] as Map<String, Any?>)["fields"] as Map<String, Map<String, Any?>>
        val parts = ArrayList<String>()
        for ((key, spec) in fields) {
            if (!full && spec["optional"] == true) continue
            parts += "\"$key\":" + sample(spec["type"], types, full)
        }
        parts += "\"a_key_the_schema_does_not_name\":1"
        return "{" + parts.joinToString(",") + "}"
    }

    @TaskAction
    fun generate() {
        @Suppress("UNCHECKED_CAST")
        val root = JsonSlurper().parseText(schema.get().asFile.readText()) as Map<String, Any?>

        @Suppress("UNCHECKED_CAST")
        val methods = root["methods"] as List<Map<String, Any?>>

        @Suppress("UNCHECKED_CAST")
        val events = root["events"] as List<Map<String, Any?>>

        @Suppress("UNCHECKED_CAST")
        val types = (root["types"] as? Map<String, Any?>) ?: emptyMap()
        val header = "// Generated by the build from schema/backend-ipc.json (protocol ${root["protocol"]}); do not edit."

        // ---- the result types
        val typesOut = StringBuilder()
        typesOut.appendLine(header)
        typesOut.appendLine("package dev.mytop.pythia.ui.backend")
        typesOut.appendLine()
        typesOut.appendLine("import kotlinx.serialization.SerialName")
        typesOut.appendLine("import kotlinx.serialization.Serializable")
        typesOut.appendLine("import kotlinx.serialization.json.JsonElement")
        for ((name, definition) in types.toSortedMap()) {
            @Suppress("UNCHECKED_CAST")
            val fields = (definition as Map<String, Any?>)["fields"] as Map<String, Map<String, Any?>>
            typesOut.appendLine()
            typesOut.appendLine("@Serializable")
            typesOut.appendLine("data class $name(")
            for ((key, spec) in fields) {
                var type = typeOf(spec["type"])
                if (spec["optional"] == true && !type.endsWith("?")) type += "?"
                val default = if (type.endsWith("?")) " = null" else ""
                typesOut.appendLine("    @SerialName(\"$key\") val ${camel(key)}: $type$default,")
            }
            typesOut.appendLine(")")
        }

        // ---- the client
        val out = StringBuilder()
        out.appendLine(header)
        out.appendLine("package dev.mytop.pythia.ui.backend")
        out.appendLine()
        out.appendLine("import dev.droidtop.lib.ui.contract.Answer")
        out.appendLine("import dev.mytop.pythia.ui.BackendChannel")
        out.appendLine("import dev.mytop.pythia.ui.BackendEvent")
        out.appendLine("import dev.mytop.pythia.ui.TypedAnswer")
        out.appendLine("import dev.mytop.pythia.ui.typed")
        out.appendLine("import dev.mytop.pythia.ui.typedEvents")
        out.appendLine("import kotlinx.coroutines.flow.Flow")
        out.appendLine("import kotlinx.serialization.json.JsonArray")
        out.appendLine("import kotlinx.serialization.json.JsonElement")
        out.appendLine("import kotlinx.serialization.json.buildJsonObject")
        out.appendLine("import kotlinx.serialization.json.put")
        out.appendLine()
        out.appendLine("const val BACKEND_SCHEMA_PROTOCOL = ${root["protocol"]}")
        out.appendLine()
        out.appendLine("/** Every method of Pythia's Backend, as it answers over the channel. A call that settles later waits for it. */")
        out.appendLine("class PythiaBackend(private val channel: BackendChannel) {")
        out.appendLine("    /** Every event the Backend pushes, as it arrives. */")
        out.appendLine("    val events: Flow<BackendEvent> get() = channel.events")
        for (event in events) {
            val name = event["name"] as String
            val payload = event["payload"] as String?
            out.appendLine()
            if (payload != null) {
                out.appendLine("    /** The payload of each `$name` event. */")
                out.appendLine("    val ${camel(name)}: Flow<$payload> get() = channel.eventsNamed(\"$name\").typedEvents($payload.serializer())")
            } else {
                out.appendLine("    /** The arguments of each `$name` event. */")
                out.appendLine("    val ${camel(name)}: Flow<JsonArray> get() = channel.eventsNamed(\"$name\")")
            }
        }
        for (method in methods) {
            val name = method["name"] as String
            val result = method["result"] as String?

            @Suppress("UNCHECKED_CAST")
            val params = method["params"] as List<Map<String, Any?>>
            val backendSignature = params.joinToString(", ") { p ->
                val optional = p["optional"] == true
                val type = kotlinType(p["type"] as String) + if (optional) "?" else ""
                "${camel(p["name"] as String)}: $type" + if (optional) " = null" else ""
            }
            val signature = listOf(backendSignature, "timeoutMs: Long = 60_000").filter { it.isNotEmpty() }.joinToString(", ")
            val returns = if (result != null) "TypedAnswer<$result>" else "Answer"
            val finish = if (result != null) ".typed($result.serializer())" else ""
            out.appendLine()
            out.appendLine("    suspend fun ${camel(name)}($signature): $returns =")
            if (params.isEmpty()) {
                out.appendLine("        channel.call(\"$name\", buildJsonObject { }, timeoutMs = timeoutMs)$finish")
            } else {
                out.appendLine("        channel.call(")
                out.appendLine("            \"$name\",")
                out.appendLine("            buildJsonObject {")
                for (p in params) {
                    val key = p["name"] as String
                    val arg = camel(key)
                    if (p["optional"] == true) {
                        out.appendLine("                $arg?.let { put(\"$key\", it) }")
                    } else {
                        out.appendLine("                put(\"$key\", $arg)")
                    }
                }
                out.appendLine("            },")
                out.appendLine("            timeoutMs = timeoutMs,")
                out.appendLine("        )$finish")
            }
        }
        out.appendLine("}")

        // ---- the samples the tests decode
        val samples = StringBuilder()
        samples.appendLine(header)
        samples.appendLine("package dev.mytop.pythia.ui.backend")
        samples.appendLine()
        samples.appendLine("import dev.mytop.pythia.ui.BackendJson")
        samples.appendLine("import kotlinx.serialization.json.JsonElement")
        samples.appendLine()
        samples.appendLine("/** For each typed method and event: a full and a sparse sample made from its schema type, and how to read it. */")
        samples.appendLine("object BackendSamples {")
        samples.appendLine("    class Sample(val name: String, val texts: List<String>, val decode: (JsonElement) -> Any)")
        samples.appendLine()
        samples.appendLine("    val answers: List<Sample> = listOf(")
        for (method in methods) {
            val result = method["result"] as String? ?: continue
            samples.appendLine("        Sample(")
            samples.appendLine("            \"${method["name"]}\",")
            samples.appendLine("            listOf(")
            samples.appendLine("                \"\"\"${objectSample(result, types, true)}\"\"\",")
            samples.appendLine("                \"\"\"${objectSample(result, types, false)}\"\"\",")
            samples.appendLine("            ),")
            samples.appendLine("        ) { BackendJson.decodeFromJsonElement($result.serializer(), it) },")
        }
        samples.appendLine("    )")
        samples.appendLine()
        samples.appendLine("    val events: List<Sample> = listOf(")
        for (event in events) {
            val payload = event["payload"] as String? ?: continue
            samples.appendLine("        Sample(")
            samples.appendLine("            \"${event["name"]}\",")
            samples.appendLine("            listOf(")
            samples.appendLine("                \"\"\"${objectSample(payload, types, true)}\"\"\",")
            samples.appendLine("                \"\"\"${objectSample(payload, types, false)}\"\"\",")
            samples.appendLine("            ),")
            samples.appendLine("        ) { BackendJson.decodeFromJsonElement($payload.serializer(), it) },")
        }
        samples.appendLine("    )")
        samples.appendLine("}")

        val dir = outputDir.get().asFile
        dir.deleteRecursively()
        dir.mkdirs()
        dir.resolve("PythiaBackend.kt").writeText(out.toString())
        dir.resolve("BackendTypes.kt").writeText(typesOut.toString())
        val testDir = testOutputDir.get().asFile
        testDir.deleteRecursively()
        testDir.mkdirs()
        testDir.resolve("BackendSamples.kt").writeText(samples.toString())
    }
}

val generateBackendClient = tasks.register<GenerateBackendClient>("generateBackendClient") {
    schema.set(rootProject.layout.projectDirectory.file("../schema/backend-ipc.json"))
    outputDir.set(layout.buildDirectory.dir("generated/backend/commonMain/kotlin"))
    testOutputDir.set(layout.buildDirectory.dir("generated/backend/commonTest/kotlin"))
}

// Everything that talks to Pythia, with no UI: the framing, the channel, the generated client and the connection. Its
// tests link and run on their own (a test binary of the app would need the window library, SDL3).
kotlin {
    linuxX64()
    linuxArm64()
    mingwX64()
    macosX64()
    macosArm64()

    // A command-line client with no window, for every target: --probe and --watch (the integration job runs it on macOS,
    // where the compose-native fork has no artifacts yet).
    targets.withType<KotlinNativeTarget>().configureEach {
        binaries.executable {
            entryPoint = "dev.mytop.pythia.ui.probeMain"
            baseName = "pythia-probe"
        }
    }

    // Linux and macOS compile the one posix transport (src/posixShared) into both of their source sets; only the connect
    // call differs, in each of them (sockaddr_un is platform.linux's on Linux, platform.posix's, with sun_len, on macOS).
    sourceSets {
        configureEach {
            if (name == "linuxMain" || name == "macosMain") kotlin.srcDir("src/posixShared/kotlin")
        }
    }

    sourceSets {
        commonMain {
            kotlin.srcDir(generateBackendClient.flatMap { it.outputDir })
            dependencies {
                api("org.jetbrains.kotlinx:kotlinx-coroutines-core:1.10.2")
                api("org.jetbrains.kotlinx:kotlinx-serialization-json:1.9.0")
                api("dev.droidtop.lib.ui:contract:$uiLibVersion")
            }
        }
        commonTest {
            kotlin.srcDir(generateBackendClient.flatMap { it.testOutputDir })
            dependencies {
                implementation(kotlin("test"))
                implementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.10.2")
            }
        }
    }
}
