package dev.mytop.pythia.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.input.key.*
import androidx.compose.ui.unit.dp
import dev.droidtop.lib.ui.contract.Answer
import dev.droidtop.lib.ui.format.ValueFormat
import dev.droidtop.shell.gamepad.ShellLoading
import dev.droidtop.shell.gamepad.ShellSwitch
import dev.droidtop.shell.gamepad.rowFocus
import dev.mytop.pythia.ui.backend.Card
import dev.mytop.pythia.ui.backend.GetGameDetailResult
import kotlinx.coroutines.launch
import kotlinx.serialization.json.*

/** The native library calls the existing core; it never opens game files or guesses an action's result. */
@Composable
@OptIn(ExperimentalLayoutApi::class)
fun LibraryScreen(connection: PythiaConnection) {
    val state by connection.state.collectAsState()
    var page by remember { mutableStateOf("Library") }
    var selected by remember { mutableStateOf<String?>(null) }
    var query by remember { mutableStateOf("") }
    var installed by remember { mutableStateOf(false) }
    var favourites by remember { mutableStateOf(false) }
    Column(Modifier.fillMaxSize().padding(16.dp).onPreviewKeyEvent {
        if (it.type == KeyEventType.KeyDown && it.key == Key.Escape && selected != null) {
            selected = null
            true
        } else false
    }, verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text("Pythia", style = MaterialTheme.typography.headlineMedium)
        // FlowRow keeps every destination reachable at a phone-sized width.
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            for (name in listOf("Library", "Folders", "Activity", "Diagnostics")) {
                FilterChip(selected = page == name, onClick = { page = name; selected = null }, label = { Text(name) })
            }
        }
        when (val s = state) {
            PythiaState.Connecting -> { ShellLoading(); Text("Connecting to Pythia…") }
            is PythiaState.Unavailable -> {
                Text(s.reason)
                Button(onClick = connection::retryNow) { Text("Try again") }
            }
            is PythiaState.Ready -> when (page) {
                "Library" -> if (selected != null) {
                    GamePage(connection, selected!!, onBack = { selected = null })
                } else {
                    OutlinedTextField(value = query, onValueChange = { query = it }, label = { Text("Search games") },
                        singleLine = true, modifier = Modifier.fillMaxWidth())
                    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        FilterChip(installed, { installed = !installed }, label = { Text("Installed") })
                        FilterChip(favourites, { favourites = !favourites }, label = { Text("Favourites") })
                        TextButton(onClick = connection::refreshGames) { Text("Refresh") }
                    }
                    when (val answer = s.games) {
                        null -> { ShellLoading(); Text("Reading your library…") }
                        is TypedAnswer.Other -> AnswerMessage(answer.answer)
                        is TypedAnswer.Ok -> {
                            LaunchedEffect(answer.value.version) { uiLog("ui: rendered library games=${answer.value.games.size}") }
                            val games = remember(answer.value, query, installed, favourites) {
                                answer.value.games.filter {
                                    it.title.contains(query.trim(), ignoreCase = true) &&
                                        (!installed || it.installed) && (!favourites || it.favourite)
                                }
                            }
                            Text("${ValueFormat.count(games.size.toLong())} games")
                            if (answer.value.games.isEmpty()) {
                                Text("Your library is empty. Add a game folder in Folders to get started.")
                                Button(onClick = { page = "Folders" }) { Text("Add a folder") }
                            } else if (games.isEmpty()) {
                                Text("No games match. Clear the search or filters to see more games.")
                                TextButton(onClick = { query = ""; installed = false; favourites = false }) { Text("Clear search and filters") }
                            }
                            LazyColumn(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                                items(games, key = { it.id }) { game ->
                                    GameRow(game) { selected = game.id }
                                }
                            }
                        }
                    }
                }
                "Folders" -> FoldersPage(connection)
                "Activity" -> ActivityPage(connection)
                else -> {
                    AnswerMessage(s.systemInfo)
                    val events by connection.events.collectAsState()
                    LazyColumn { items(events, key = { it.number }) { Text("${it.name}: ${it.args}") } }
                }
            }
        }
    }
}

@Composable
private fun GameRow(game: Card, open: () -> Unit) {
    var focused by remember { mutableStateOf(false) }
    Column(Modifier.fillMaxWidth().onFocusChanged { focused = it.isFocused }.rowFocus(focused)
        .clickable(onClick = open).padding(12.dp)) {
        Text(game.title, style = MaterialTheme.typography.titleMedium)
        Text(listOfNotNull(if (game.installed) "Installed" else "Not installed", game.runner,
            if (game.favourite) "Favourite" else null).joinToString(" · "), color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun GamePage(connection: PythiaConnection, id: String, onBack: () -> Unit) {
    var detail by remember(id) { mutableStateOf<TypedAnswer<GetGameDetailResult>?>(null) }
    var play by remember(id) { mutableStateOf<Answer?>(null) }
    var choices by remember(id) { mutableStateOf<Answer?>(null) }
    var showChoices by remember(id) { mutableStateOf(false) }
    var running by remember(id) { mutableStateOf(false) }
    var result by remember(id) { mutableStateOf<Answer?>(null) }
    var busy by remember(id) { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    suspend fun reload() {
        detail = connection.gameDetail(id)
        play = connection.call { getPlayState(id) }
        val active = connection.call { getRunningGames().let { (it as? TypedAnswer.Ok)?.answer ?: (it as TypedAnswer.Other).answer } }
        running = ((active as? Answer.Ok)?.fields?.get("games") as? JsonArray)?.any {
            it.jsonObject["game_id"]?.text() == id && it.jsonObject["quittable"]?.text() == "true"
        } == true
    }
    LaunchedEffect(id) { reload() }
    LazyColumn(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        item { TextButton(onClick = onBack) { Text("Back to library") } }
        when (val d = detail) {
            null -> item { ShellLoading() }
            is TypedAnswer.Other -> item { AnswerMessage(d.answer) }
            is TypedAnswer.Ok -> {
                val game = d.value.fields
                item { Text(game.title, style = MaterialTheme.typography.headlineMedium) }
                item { Text((game.platforms + game.runners).joinToString(" · ")) }
                item {
                    val primary = (play as? Answer.Ok)?.fields?.get("primary") as? JsonObject
                    val action = primary?.get("action")?.text() ?: "Play"
                    Button(enabled = !busy && primary?.get("enabled")?.text() == "true", onClick = {
                        scope.launch {
                            busy = true
                            try { result = connection.call { playGame(id) }; connection.refreshGames(); reload() }
                            finally { busy = false }
                        }
                    }) { Text(if (busy) "Working…" else action.replaceFirstChar { it.uppercase() }) }
                    if (play !is Answer.Ok) AnswerMessage(play)
                    primary?.get("reason")?.text()?.takeIf { it.isNotBlank() && it != "null" }?.let { Text(it) }
                    result?.let { AnswerMessage(it, "Done") }
                }
                item {
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Button(enabled = !busy, onClick = {
                            scope.launch {
                                busy = true
                                try { result = connection.call { setFavourite(id, !game.favourite) }; reload(); connection.refreshGames() }
                                finally { busy = false }
                            }
                        }) { Text(if (game.favourite) "Remove favourite" else "Favourite") }
                        Button(enabled = !busy && running, onClick = {
                            scope.launch {
                                busy = true
                                try { result = connection.call { quitGame(id) } }
                                finally { busy = false }
                            }
                        }) { Text("Quit game") }
                    }
                }
                item {
                    TextButton(enabled = !busy, onClick = {
                        showChoices = !showChoices
                        if (showChoices) scope.launch { choices = connection.call { getLaunchChoices(id) } }
                    }) { Text("How it runs") }
                }
                if (showChoices) {
                    item { AnswerMessage(choices) }
                    val fields = (choices as? Answer.Ok)?.fields
                    val candidates = fields?.get("candidates") as? JsonArray ?: JsonArray(emptyList())
                    val vias = fields?.get("vias") as? JsonArray ?: JsonArray(emptyList())
                    items(candidates) { candidate ->
                        val exe = candidate.jsonObject["exe"]?.text().orEmpty()
                        Column {
                            Text(exe)
                            for (via in vias) TextButton(enabled = !busy, onClick = {
                                scope.launch {
                                    busy = true
                                    try { result = connection.call { setLaunch(id, exe, via.text()) }; reload() }
                                    finally { busy = false }
                                }
                            }) { Text("Run ${if (via.text() == "wine") "with Wine" else "natively"}") }
                        }
                    }
                    if (fields != null) item {
                        TextButton(enabled = !busy, onClick = { scope.launch {
                            busy = true
                            try { result = connection.call { setLaunch(id, "", "native") }; reload() }
                            finally { busy = false }
                        } }) { Text("Use automatic choice") }
                    }
                }
                item { Text("Played ${game.playcount} times · ${game.playtimeHours} hours") }
                if (game.notes.isNotBlank()) item { Text(game.notes) }
                item { Text("Versions", style = MaterialTheme.typography.titleMedium) }
                items(game.versions) { Text(it.version ?: "Version not recorded") }
                if (game.stores.isNotEmpty()) item { Text(game.stores.joinToString(" · ") { it.platform }) }
            }
        }
    }
}

@Composable
private fun FoldersPage(connection: PythiaConnection) {
    var answer by remember { mutableStateOf<Answer?>(null) }
    var result by remember { mutableStateOf<Answer?>(null) }
    var path by remember { mutableStateOf("") }
    var roms by remember { mutableStateOf(false) }
    var busy by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    LaunchedEffect(connection) { answer = connection.call { getLibraryFolders() } }
    LazyColumn(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        item { Text("Library folders", style = MaterialTheme.typography.titleLarge) }
        item { OutlinedTextField(path, { path = it }, label = { Text("Folder path on this computer") }, modifier = Modifier.fillMaxWidth()) }
        item { Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            TextButton(onClick = { roms = !roms }) { ShellSwitch(roms); Spacer(Modifier.width(8.dp)); Text("Console ROMs") }
            Button(enabled = path.isNotBlank() && !busy, onClick = {
                scope.launch {
                    busy = true
                    try {
                        result = connection.call { addLibraryFolder(path.trim(), if (roms) "roms" else "games") }
                        if (result is Answer.Ok) { path = ""; answer = connection.call { getLibraryFolders() }; connection.refreshGames() }
                    } finally { busy = false }
                }
            }) { Text(if (busy) "Adding…" else "Add folder") }
        } }
        item { result?.let { AnswerMessage(it, "Folder added. Games are being scanned.") }; AnswerMessage(answer) }
        val folders = (answer as? Answer.Ok)?.fields?.get("folders") as? JsonArray ?: JsonArray(emptyList())
        if (answer is Answer.Ok && folders.isEmpty()) item { Text("No folders added yet.") }
        items(folders) { folder ->
            val row = folder.jsonObject
            val folderPath = row["path"]?.text().orEmpty()
            Column {
                Text(folderPath)
                Text(if (row["present"]?.text() == "true") row["kind"]?.text().orEmpty() else "Folder is not available on this computer")
                TextButton(enabled = !busy, onClick = {
                    scope.launch {
                        busy = true
                        try { result = connection.call { removeLibraryFolder(folderPath) }; answer = connection.call { getLibraryFolders() } }
                        finally { busy = false }
                    }
                }) { Text("Stop tracking folder") }
            }
        }
        item { Text("Stopping tracking leaves the folder, its files and games already added untouched.") }
    }
}

@Composable
private fun ActivityPage(connection: PythiaConnection) {
    var answer by remember { mutableStateOf<Answer?>(null) }
    var result by remember { mutableStateOf<Answer?>(null) }
    val events by connection.events.collectAsState()
    val scope = rememberCoroutineScope()
    LaunchedEffect(events.firstOrNull { it.name == "jobsChanged" }?.number) { answer = connection.call { getJobs() } }
    LazyColumn(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        item { Text("Activity", style = MaterialTheme.typography.titleLarge) }
        item { TextButton(onClick = { scope.launch { result = connection.call { clearFinishedJobs() }; answer = connection.call { getJobs() } } }) { Text("Clear finished jobs") } }
        item { AnswerMessage(answer); result?.let { AnswerMessage(it) } }
        val jobs = (answer as? Answer.Ok)?.fields?.get("jobs") as? JsonArray ?: JsonArray(emptyList())
        if (answer is Answer.Ok && jobs.isEmpty()) item { Text("No jobs yet.") }
        items(jobs) { job ->
            val row = job.jsonObject
            Column {
                Text(row["title"]?.text() ?: row["label"]?.text() ?: "Job")
                Text(row["state"]?.text().orEmpty())
                if (row["state"]?.text() == "running") TextButton(onClick = {
                    scope.launch { result = connection.call { cancelJob(row["id"]?.text().orEmpty()) }; answer = connection.call { getJobs() } }
                }) { Text("Cancel") }
            }
        }
    }
}

@Composable
private fun AnswerMessage(answer: Answer?, success: String? = null) {
    when (answer) {
        null -> ShellLoading()
        is Answer.Ok -> if (success != null) Text(success)
        is Answer.Error -> Text(answer.message, color = MaterialTheme.colorScheme.error)
        is Answer.Unavailable -> Text(answer.message)
        is Answer.Pending -> { ShellLoading(); Text("Working…") }
    }
}

private fun JsonElement.text(): String = (this as? JsonPrimitive)?.content.orEmpty()
