package dev.mytop.pythia.ui

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import dev.droidtop.lib.ui.contract.Answer
import dev.droidtop.shell.gamepad.ShellSwitch
import kotlinx.coroutines.launch
import kotlinx.serialization.json.*

/** Legacy inventory slots return a list, carried under value by the shared contract. */
private fun inventory(answer: Answer?): List<JsonObject> =
    ((answer as? Answer.Ok)?.fields?.get("value") as? JsonArray).orEmpty().mapNotNull { it as? JsonObject }

private val categories = mapOf("library" to "Game source", "engine" to "Engine", "downloader" to "Downloader",
    "manager" to "Launcher", "metadata" to "Game information", "mod" to "Mods", "patcher" to "Patcher", "source" to "Remote source")

@Composable
internal fun PluginsPage(connection: PythiaConnection) {
    var answer by remember { mutableStateOf<Answer?>(null) }
    var result by remember { mutableStateOf<Answer?>(null) }
    var query by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()
    LaunchedEffect(connection) { answer = connection.call { getAllPlugins() } }
    LazyColumn(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        item { Text("Plugins", style = MaterialTheme.typography.titleLarge) }
        item { OutlinedTextField(query, { query = it }, label = { Text("Find a plugin") }, modifier = Modifier.fillMaxWidth(), singleLine = true) }
        item { AnswerMessage(answer); result?.let { AnswerMessage(it) } }
        val all = inventory(answer)
        val rows = all.filter { it["display_name"]?.text().orEmpty().contains(query.trim(), ignoreCase = true) }
        if (answer is Answer.Ok && rows.isEmpty()) item { Text(if (all.isEmpty()) "No plugins are installed." else "No plugins match your search.") }
        items(rows, key = { it["id"]!!.text() }) { row ->
            val id = row["id"]!!.text()
            val enabled = row["enabled"]?.text() == "true"
            Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text(row["display_name"]?.text().orEmpty(), style = MaterialTheme.typography.titleMedium)
                val kinds = (row["categories"] as? JsonArray).orEmpty().map { categories[it.text()] ?: "Other" }
                Text((kinds + listOfNotNull(row["version"]?.text(), row["origin"]?.text())).filter { it.isNotBlank() }.joinToString(" · "))
                TextButton(enabled = busy == null, onClick = {
                    scope.launch {
                        busy = id
                        try {
                            result = connection.call { setPluginEnabled(id, !enabled) }
                            answer = connection.call { getAllPlugins() }
                        } finally { busy = null }
                    }
                }) {
                    ShellSwitch(enabled)
                    Spacer(Modifier.width(8.dp))
                    Text(if (busy == id) "Changing…" else if (enabled) "Enabled" else "Disabled")
                }
            }
        }
    }
}

@Composable
internal fun AccountsPage(connection: PythiaConnection) {
    var answer by remember { mutableStateOf<Answer?>(null) }
    var result by remember { mutableStateOf<Answer?>(null) }
    var busy by remember { mutableStateOf(false) }
    val events by connection.events.collectAsState()
    val scope = rememberCoroutineScope()
    LaunchedEffect(events.firstOrNull { it.name == "accountSyncComplete" }?.number) {
        answer = connection.call { getAccountPlugins() }
    }
    LazyColumn(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        item { Text("Accounts", style = MaterialTheme.typography.titleLarge) }
        item {
            TextButton(enabled = !busy, onClick = { scope.launch {
                busy = true
                try { result = connection.call { syncAllAccounts() } } finally { busy = false }
            } }) { Text("Sync connected accounts") }
            AnswerMessage(answer)
            result?.let { AnswerMessage(it, "Sync requested. See Activity for progress.") }
        }
        val rows = inventory(answer)
        if (answer is Answer.Ok && rows.isEmpty()) item { Text("No installed source provides an account.") }
        items(rows, key = { it["id"]!!.text() }) { row ->
            AccountRow(connection, row) { scope.launch { answer = connection.call { getAccountPlugins() } } }
        }
    }
}

@Composable
private fun AccountRow(connection: PythiaConnection, row: JsonObject, reload: () -> Unit) {
    val id = row["id"]!!.text()
    var result by remember(id) { mutableStateOf<Answer?>(null) }
    var busy by remember(id) { mutableStateOf(false) }
    var pasted by remember(id) { mutableStateOf("") }
    val scope = rememberCoroutineScope()
    val browser = row["browser"] as? JsonObject
    val connected = row["connected"]?.text() == "true"
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text(row["display_name"]?.text().orEmpty(), style = MaterialTheme.typography.titleMedium)
        Text(if (connected) "Connected" else "Not connected")
        if (browser != null && browser.isNotEmpty()) {
            TextButton(enabled = !busy, onClick = { scope.launch {
                busy = true
                try { result = connection.call { openAccountLogin(id) } } finally { busy = false }
            } }) { Text("Sign in with your browser") }
            OutlinedTextField(pasted, { pasted = it }, label = { Text(browser["paste"]?.text() ?: "Paste the sign-in response") },
                modifier = Modifier.fillMaxWidth(), visualTransformation = PasswordVisualTransformation(), singleLine = true)
            Button(enabled = !busy && pasted.isNotBlank(), onClick = { scope.launch {
                busy = true
                try {
                    result = connection.call { finishAccountConnect(id, pasted.trim()) }
                    if (result is Answer.Ok) { pasted = ""; reload() }
                } finally { busy = false }
            } }) { Text("Finish sign-in") }
        }
        for (field in (row["fields"] as? JsonArray).orEmpty()) {
            val spec = field.jsonObject
            val key = spec["key"]!!.text()
            var value by remember(id, key) { mutableStateOf("") }
            OutlinedTextField(value, { value = it }, label = { Text(spec["label"]?.text() ?: "Account value") },
                modifier = Modifier.fillMaxWidth(), singleLine = true,
                visualTransformation = if (spec["secret"]?.text() == "true") PasswordVisualTransformation() else VisualTransformation.None)
            TextButton(enabled = !busy && value.isNotBlank(), onClick = { scope.launch {
                busy = true
                try {
                    result = connection.call { setAccountCredential(id, key, value) }
                    if (result is Answer.Ok) { value = ""; reload() }
                } finally { busy = false }
            } }) { Text("Save ${spec["label"]?.text() ?: "account value"}") }
        }
        if (connected) TextButton(enabled = !busy, onClick = { scope.launch {
            busy = true
            try { result = connection.call { syncAccount(id) } } finally { busy = false }
        } }) { Text("Sync library") }
        result?.let { AnswerMessage(it) }
    }
}
