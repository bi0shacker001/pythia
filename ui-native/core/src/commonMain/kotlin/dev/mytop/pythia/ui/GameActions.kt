package dev.mytop.pythia.ui

import dev.droidtop.lib.ui.contract.Answer
import kotlinx.serialization.json.*

/** A successful read does not mean Play is enabled: the primary action carries its own availability. */
data class PrimaryAction(val label: String, val enabled: Boolean, val reason: String?)

fun primaryAction(answer: Answer?): PrimaryAction {
    if (answer !is Answer.Ok) return PrimaryAction("Play", false, when (answer) {
        is Answer.Error -> answer.message
        is Answer.Unavailable -> answer.message
        else -> null
    })
    val primary = answer.fields["primary"] as? JsonObject
        ?: return PrimaryAction("Play", false, "Pythia did not report how this game starts.")
    val action = (primary["action"] as? JsonPrimitive)?.contentOrNull
    val label = when (action) { "play" -> "Play"; "install" -> "Install"; "update" -> "Update"; else -> null }
    return PrimaryAction(label ?: "Play", label != null && (primary["enabled"] as? JsonPrimitive)?.booleanOrNull == true,
        (primary["reason"] as? JsonPrimitive)?.contentOrNull
            ?: if (label == null) "Pythia reported an unknown launch action." else null)
}

data class LaunchOption(val exe: String, val via: String)

/** Linux programs cannot be run through Wine; on Windows only Windows programs are native candidates. */
fun launchOptions(answer: Answer?): List<LaunchOption> {
    if (answer !is Answer.Ok) return emptyList()
    val candidates = answer.fields["candidates"] as? JsonArray ?: return emptyList()
    val vias = (answer.fields["vias"] as? JsonArray).orEmpty().mapNotNull { (it as? JsonPrimitive)?.contentOrNull }.toSet()
    val windows = (answer.fields["windows"] as? JsonPrimitive)?.booleanOrNull == true
    return candidates.flatMap { value ->
        val row = value as? JsonObject ?: return@flatMap emptyList()
        val exe = (row["exe"] as? JsonPrimitive)?.contentOrNull?.takeIf { it.isNotBlank() } ?: return@flatMap emptyList()
        val kind = (row["kind"] as? JsonPrimitive)?.contentOrNull
        buildList {
            if ("native" in vias && kind == if (windows) "windows" else "linux") add(LaunchOption(exe, "native"))
            if (!windows && "wine" in vias && kind == "windows") add(LaunchOption(exe, "wine"))
        }
    }
}
