package dev.mytop.pythia.ui

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import dev.droidtop.lib.ui.contract.Answer
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.serialization.json.*

internal fun Answer?.rows(name: String): List<JsonObject> =
    ((this as? Answer.Ok)?.fields?.get(name) as? JsonArray).orEmpty().mapNotNull { it as? JsonObject }

private data class PairingRequest(val mode: String, val code: String = "")

@Composable
internal fun DevicesPage(connection: PythiaConnection) {
    var devices by remember { mutableStateOf<Answer?>(null) }
    var found by remember { mutableStateOf<Answer?>(null) }
    var result by remember { mutableStateOf<Answer?>(null) }
    var status by remember { mutableStateOf<Answer?>(null) }
    var invitation by remember { mutableStateOf<Answer?>(null) }
    var mode by remember { mutableStateOf("") }
    var code by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var forgetting by remember { mutableStateOf<JsonObject?>(null) }
    var networkRequest by remember { mutableStateOf<PairingRequest?>(null) }
    var networkResult by remember { mutableStateOf<Answer?>(null) }
    var checkingNetwork by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    val events by connection.events.collectAsState()
    suspend fun startPairing(request: PairingRequest) {
        if (request.mode == "typed") {
            result = connection.call { pairWithCode(request.code) }
            if (result is Answer.Ok) code = ""
        } else mode = request.mode
    }
    fun requestPairing(request: PairingRequest) {
        if (busy) return
        busy = true
        checkingNetwork = true
        scope.launch {
            try {
                when (val decision = pairingNetworkDecision(connection.call { pairingNetworkExplained() })) {
                    PairingNetworkDecision.Ready -> startPairing(request)
                    PairingNetworkDecision.Explain -> { networkResult = null; networkRequest = request }
                    is PairingNetworkDecision.Blocked -> result = decision.answer
                }
            } finally { checkingNetwork = false; busy = false }
        }
    }
    LaunchedEffect(events.firstOrNull { it.name == "handheldsChanged" }?.number) {
        devices = connection.call { getHandhelds() }
    }
    // Serialize withdrawal while this connection survives. A lost IPC client still needs a backend-owned offer lease.
    LaunchedEffect(mode) {
        val activeMode = mode
        if (activeMode.isNotEmpty()) {
            invitation = null
            val owned = connection.withPairingSession(activeMode == "compare") { initial ->
                status = initial
                invitation = status
                if (status is Answer.Ok) while (true) {
                    delay(1_000)
                    status = call { if (activeMode == "compare") pairCompareStatus() else pairingStatus() }
                    val state = (status as? Answer.Ok)?.fields?.get("state")?.text()
                    if (state == "paired" || state == "failed" || status !is Answer.Ok) {
                        devices = call { getHandhelds() }
                        break
                    }
                }
            }
            if (!owned) status = Answer.Unavailable("Pythia is not connected. Try pairing again after it reconnects.")
        }
    }
    LazyColumn(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        item { Text("Devices", style = MaterialTheme.typography.titleLarge) }
        item { Text("Pairing connects your library and saves to another device. Compare the code on both screens before agreeing.") }
        item {
            Button(enabled = !busy && mode.isEmpty(), onClick = { requestPairing(PairingRequest("compare")) }) { Text("Pair on this network") }
            TextButton(enabled = !busy && mode.isEmpty(), onClick = { requestPairing(PairingRequest("code")) }) { Text("Show a code for a handheld") }
            if (checkingNetwork) LoadingStatus("Checking pairing…")
            if (mode.isNotEmpty()) TextButton(onClick = { mode = ""; status = null; found = null }) { Text("Stop pairing") }
        }
        if (mode.isNotEmpty()) {
            item {
                AnswerMessage(status)
                val fields = (status as? Answer.Ok)?.fields
                (fields?.get("code") ?: (invitation as? Answer.Ok)?.fields?.get("code"))?.text()
                    ?.takeIf { it.isNotBlank() }?.let { Text(it, style = MaterialTheme.typography.headlineLarge) }
                fields?.get("name")?.text()?.let { Text(it) }
                when (fields?.get("state")?.text()) {
                    "paired" -> Text("Paired with ${fields?.get("paired")?.text().orEmpty()}")
                    "failed" -> Text(fields?.get("error")?.text() ?: "Pairing did not finish.", color = MaterialTheme.colorScheme.error)
                    "finishing" -> Text("Waiting for the other device…")
                    "comparing" -> {
                        Text("Do both screens show this code?")
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            for ((same, label) in listOf(false to "Different codes", true to "Same code")) {
                                Button(enabled = !busy, onClick = { scope.launch {
                                    busy = true
                                    try {
                                        result = connection.call { confirmPairing(same, timeoutMs = 150_000) }
                                        if (result is Answer.Ok) { mode = ""; devices = connection.call { getHandhelds() } }
                                    } finally { busy = false }
                                } }) { Text(label) }
                            }
                        }
                    }
                    else -> Text("Waiting for another device…")
                }
            }
            if (mode == "compare") {
                item { TextButton(enabled = !busy, onClick = { scope.launch {
                    busy = true
                    try { found = connection.call { findComputers() } } finally { busy = false }
                } }) { Text("Find computers") }; found?.let { AnswerMessage(it) } }
                items(found.rows("computers")) { computer ->
                    TextButton(enabled = !busy, onClick = { scope.launch {
                        busy = true
                        try { result = connection.call { pickComputer(computer) } } finally { busy = false }
                    } }) { Text(computer["name"]?.text() ?: "Computer") }
                }
            }
        }
        item {
            OutlinedTextField(code, { code = it }, label = { Text("Code shown on a handheld") }, singleLine = true, modifier = Modifier.fillMaxWidth())
            TextButton(enabled = !busy && mode.isEmpty() && code.isNotBlank(),
                onClick = { requestPairing(PairingRequest("typed", code.trim())) }) { Text("Pair using this code") }
            result?.let { AnswerMessage(it) }
            (result as? Answer.Ok)?.fields?.let { feedback ->
                if (feedback["job"] != null) Text("Pairing started. See Activity for progress.")
                feedback["paired"]?.text()?.let { Text("Paired with $it") }
            }
            AnswerMessage(devices)
        }
        val rows = devices.rows("devices")
        if (devices is Answer.Ok && rows.isEmpty()) item { Text("No devices are paired yet.") }
        items(rows) { device ->
            val id = device["id"]?.text().orEmpty()
            Column {
                Text(device["name"]?.text() ?: "Paired device", style = MaterialTheme.typography.titleMedium)
                val offers = device["offered"] as? JsonObject
                for ((key, label) in listOf("library" to "Share library", "saves" to "Share saves", "launch" to "Allow starting games here")) {
                    val on = (if (key == "launch") device[key] else offers?.get(key))?.text() == "true"
                    ToggleRow(label, on, enabled = !busy && id.isNotBlank(), change = { scope.launch {
                        busy = true
                        try {
                            result = connection.call { if (key == "launch") setHandheldLaunch(id, !on) else setDeviceOffer(id, key, !on) }
                            devices = connection.call { getHandhelds() }
                        } finally { busy = false }
                    } })
                }
                TextButton(enabled = !busy && id.isNotBlank(), onClick = { forgetting = device }) { Text("Forget device") }
            }
        }
    }
    networkRequest?.let { request ->
        AlertDialog(
            onDismissRequest = { if (!busy) networkRequest = null },
            title = { Text("Pairing uses your network") },
            text = { Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(PAIRING_NETWORK_EXPLANATION)
                networkResult?.let { AnswerMessage(it) }
            } },
            dismissButton = { TextButton(enabled = !busy, onClick = { networkRequest = null }) { Text("Cancel") } },
            confirmButton = { TextButton(enabled = !busy, onClick = {
                if (!busy) {
                    busy = true
                    scope.launch {
                        networkResult = null
                        try {
                            val answer = connection.call { markPairingNetworkExplained() }
                            if (answer is Answer.Ok) { networkRequest = null; startPairing(request) }
                            else networkResult = answer
                        } finally { busy = false }
                    }
                }
            }) { Text(if (busy) "Working…" else "Continue") } },
        )
    }
    forgetting?.let { device ->
        AlertDialog(onDismissRequest = { forgetting = null }, title = { Text("Forget this device?") },
            text = { Text("${device["name"]?.text() ?: "This device"} will need to pair again. Your game files stay where they are.") },
            dismissButton = { TextButton(onClick = { forgetting = null }) { Text("Cancel") } },
            confirmButton = { TextButton(onClick = { forgetting = null; scope.launch {
                busy = true
                try { result = connection.call { forgetHandheld(device["id"]?.text().orEmpty()) }; devices = connection.call { getHandhelds() } }
                finally { busy = false }
            } }) { Text("Forget") } })
    }
}

@Composable
internal fun SaveBackupsPage(connection: PythiaConnection) {
    var answer by remember { mutableStateOf<Answer?>(null) }
    var result by remember { mutableStateOf<Answer?>(null) }
    var restoring by remember { mutableStateOf<JsonObject?>(null) }
    var busy by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    LaunchedEffect(connection) { answer = connection.call { getSaveBackups() } }
    LazyColumn(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        item { Text("Save backups", style = MaterialTheme.typography.titleLarge) }
        item {
            Button(enabled = !busy, onClick = { scope.launch {
                busy = true
                try { result = connection.call { backUpSavesNow() }; answer = connection.call { getSaveBackups() } }
                finally { busy = false }
            } }) { Text(if (busy) "Working…" else "Back up saves now") }
            TextButton(enabled = !busy, onClick = { scope.launch { answer = connection.call { getSaveBackups() } } }) { Text("Refresh") }
            AnswerMessage(answer)
            result?.let { AnswerMessage(it, "Finished") }
            (result as? Answer.Ok)?.fields?.let { feedback ->
                feedback["backed_up"]?.text()?.let { Text("$it games backed up") }
                feedback["files"]?.text()?.let { Text("$it save files copied") }
            }
        }
        val backups = answer.rows("backups")
        if (answer is Answer.Ok && backups.isEmpty()) item { Text("No kept save copies yet.") }
        items(backups) { copy ->
            Column {
                Text(copy["game"]?.text() ?: "Game saves", style = MaterialTheme.typography.titleMedium)
                Text(listOf("system", "last_snapshot", "state", "size").mapNotNull { copy[it]?.text()?.takeIf(String::isNotBlank) }.joinToString(" · "))
                val canRestore = copy["key"]?.text().orEmpty().isNotBlank() && copy["path"]?.text().orEmpty().isNotBlank()
                TextButton(enabled = !busy && canRestore, onClick = { restoring = copy }) { Text("Restore this copy") }
                if (!canRestore) Text("This copy has no game or file location recorded.")
            }
        }
    }
    restoring?.let { copy ->
        AlertDialog(onDismissRequest = { restoring = null }, title = { Text("Restore this save copy?") },
            text = { Text("This replaces the current saves for ${copy["game"]?.text() ?: "this game"}. Pythia keeps the current saves as another copy first.") },
            dismissButton = { TextButton(onClick = { restoring = null }) { Text("Cancel") } },
            confirmButton = { TextButton(onClick = { restoring = null; scope.launch {
                busy = true
                try {
                    result = connection.call { restoreSaveCopy(copy["key"]?.text().orEmpty(), copy["path"]?.text().orEmpty(), copy["title"]?.text().orEmpty()) }
                    answer = connection.call { getSaveBackups() }
                } finally { busy = false }
            } }) { Text("Restore") } })
    }
}
