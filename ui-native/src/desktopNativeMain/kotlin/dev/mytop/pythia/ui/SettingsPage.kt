package dev.mytop.pythia.ui

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import dev.droidtop.lib.ui.contract.Answer
import kotlinx.coroutines.launch
import kotlinx.serialization.json.*

/** Settings use backend state; a rejected write never leaves an optimistic switch enabled. */
@Composable
internal fun SettingsPage(connection: PythiaConnection) {
    var modes by remember { mutableStateOf<Answer?>(null) }
    var config by remember { mutableStateOf<Answer?>(null) }
    var result by remember { mutableStateOf<Answer?>(null) }
    var busy by remember { mutableStateOf(false) }
    var confirmation by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()
    val events by connection.events.collectAsState()
    suspend fun reload() {
        modes = connection.call { getModes() }
        config = connection.call { getUiConfig() }
    }
    fun change(action: suspend () -> Answer) {
        if (busy) return
        busy = true
        result = null
        scope.launch {
            try { result = action(); reload() } finally { busy = false }
        }
    }
    LaunchedEffect(connection, events.firstOrNull { it.name == "modesChanged" }?.number) { reload() }
    val fields = (modes as? Answer.Ok)?.fields
    val manager = fields?.get("manager") as? JsonObject
    val service = fields?.get("service") as? JsonObject
    val managerOn = (manager?.get("on") as? JsonPrimitive)?.booleanOrNull
    val managerLocked = (manager?.get("locked") as? JsonPrimitive)?.booleanOrNull
    val serviceOn = (service?.get("on") as? JsonPrimitive)?.booleanOrNull
    val serviceAvailable = (service?.get("available") as? JsonPrimitive)?.booleanOrNull
    val serviceInstalled = (service?.get("installed") as? JsonPrimitive)?.booleanOrNull
    val consent = (service?.get("consent") as? JsonPrimitive)?.contentOrNull.orEmpty()
    LazyColumn(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        item {
            Text("Settings", style = MaterialTheme.typography.titleLarge)
            TextButton(enabled = !busy, onClick = { busy = true; scope.launch {
                try { reload() } finally { busy = false }
            } }) { Text("Refresh settings") }
            result?.let { AnswerMessage(it, "Setting saved.") }
            if (busy) LoadingStatus("Saving settings…")
        }
        item {
            Text("Library management", style = MaterialTheme.typography.titleMedium)
            AnswerMessage(modes)
            if (modes is Answer.Ok && (managerOn == null || managerLocked == null)) {
                Text("Pythia returned incomplete mode settings. Refresh to try again.")
            } else if (managerOn != null && managerLocked != null) {
                Text("Manager mode keeps Downloads and intake available. Sync with Droidtop requires it to stay on.")
                ToggleRow("Manager mode", managerOn, enabled = !busy && !managerLocked) {
                    change { connection.call { setManagerMode(!managerOn) } }
                }
                if (managerLocked) Text(manager?.get("reason")?.text().orEmpty())
            }
        }
        item {
            Text("Windows security screens", style = MaterialTheme.typography.titleMedium)
            Text("Service mode lets a paired device see and answer UAC prompts and the lock and sign-in screen.")
            if (serviceOn != null && serviceAvailable != null && serviceInstalled != null) {
                Text(service?.get("state")?.text().orEmpty())
                ToggleRow("Answer Windows security screens", serviceOn,
                    enabled = !busy && serviceAvailable && (serviceOn || consent.isNotBlank())) {
                    confirmation = if (serviceOn) "disable" else "enable"
                }
                if (!serviceAvailable) Text(service?.get("reason")?.text().orEmpty())
                if (serviceAvailable && !serviceOn && consent.isBlank()) Text("The service explanation is unavailable. Refresh before enabling it.")
                if (serviceInstalled && !serviceOn) TextButton(enabled = !busy, onClick = { confirmation = "uninstall" }) {
                    Text("Remove the helper")
                }
            } else if (modes is Answer.Ok) Text("Service settings are unavailable. Refresh to try again.")
        }
        item {
            Text("Diagnostics", style = MaterialTheme.typography.titleMedium)
            AnswerMessage(config)
            val level = (((config as? Answer.Ok)?.fields?.get("log_level")) as? JsonPrimitive)?.contentOrNull
            if (level != null) {
                Text("Log detail: ${if (level == "debug") "Debug" else "Normal"}")
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    TextButton(enabled = !busy && level != "info", onClick = { change { connection.call { setLogLevel("normal") } } }) { Text("Normal") }
                    TextButton(enabled = !busy && level != "debug", onClick = { change { connection.call { setLogLevel("debug") } } }) { Text("Debug") }
                }
            } else if (config is Answer.Ok) Text("Log settings are unavailable. Refresh to try again.")
        }
    }
    confirmation?.let { action ->
        AlertDialog(onDismissRequest = { confirmation = null },
            title = { Text(when (action) { "enable" -> "Turn on service mode?"; "disable" -> "Turn off service mode?"; else -> "Remove the secure-screen helper?" }) },
            text = { Text(when (action) {
                "enable" -> consent
                "disable" -> "Windows asks for administrator approval. The helper stays installed but does nothing until you turn it on again."
                else -> "Windows asks for administrator approval. Service mode stays off."
            }, Modifier.heightIn(max = 360.dp).verticalScroll(rememberScrollState())) },
            confirmButton = { TextButton(enabled = !busy && (action != "enable" || consent.isNotBlank()), onClick = {
                confirmation = null
                change { connection.call { setServiceMode(action, timeoutMs = 150_000) } }
            }) { Text(when (action) { "enable" -> "Turn on"; "disable" -> "Turn off"; else -> "Remove" }) } },
            dismissButton = { TextButton(onClick = { confirmation = null }) { Text("Cancel") } })
    }
}
