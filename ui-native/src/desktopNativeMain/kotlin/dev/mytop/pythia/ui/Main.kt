package dev.mytop.pythia.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.DpSize
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Window
import androidx.compose.ui.window.application
import androidx.compose.ui.window.rememberWindowState
import dev.droidtop.lib.ui.contract.Answer
import dev.droidtop.lib.ui.format.ValueFormat
import dev.droidtop.lib.ui.tokens.ThemePalette
import dev.mytop.pythia.ui.backend.GetGamesResult
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlin.system.exitProcess

/**
 * Pythia's native front end, a skeleton that proves the path: the window connects to the running Pythia's backend
 * channel, asks `hello` and `get_system_info`, and lists what the backend pushes. `--probe` does the same without a
 * window, prints the answers and exits (0 when both are ok); the integration job runs it against the real Backend.
 */
fun main(args: Array<String>) {
    if ("--probe" in args) exitProcess(probe())
    if ("--watch" in args) exitProcess(watch())
    val smoke = "--smoke" in args
    application {
        val scope = rememberCoroutineScope()
        val connection = remember { PythiaConnection(scope) }
        LaunchedEffect(connection) { connection.start() }
        if (smoke) {
            // CI: once the window has what a person would see (both answers and an event), say so, hold the window
            // a few seconds for the screenshot, and leave.
            LaunchedEffect(connection) {
                val seen = withTimeoutOrNull(120_000) {
                    combine(connection.state, connection.events) { state, events ->
                        state is PythiaState.Ready && state.games is TypedAnswer.Ok && events.isNotEmpty()
                    }.first { it }
                }
                if (seen == null) {
                    uiLog("smoke: timed out")
                } else {
                    uiLog("smoke: ready")
                    delay(5_000)
                    uiLog("smoke: ok")
                }
                exitApplication()
            }
        }
        Window(
            onCloseRequest = ::exitApplication,
            title = "Pythia",
            state = rememberWindowState(size = DpSize(760.dp, 820.dp)),
        ) {
            PythiaTheme(ThemePalette.DEFAULT) {
                if ("--diagnostics" in args) PythiaScreen(connection) else LibraryScreen(connection)
            }
        }
    }
}

/** The Material scheme is the palette from mytop-ui's tokens and nothing else. */
@Composable
private fun PythiaTheme(palette: ThemePalette, content: @Composable () -> Unit) {
    val scheme = if (palette.isLight) {
        lightColorScheme(
            primary = Color(palette.accent),
            onPrimary = Color(palette.onAccent),
            background = Color(palette.ground),
            onBackground = Color(palette.onSurface),
            surface = Color(palette.card),
            onSurface = Color(palette.onSurface),
            onSurfaceVariant = Color(palette.onSurfaceMuted),
            error = Color(palette.danger),
        )
    } else {
        darkColorScheme(
            primary = Color(palette.accent),
            onPrimary = Color(palette.onAccent),
            background = Color(palette.ground),
            onBackground = Color(palette.onSurface),
            surface = Color(palette.card),
            onSurface = Color(palette.onSurface),
            onSurfaceVariant = Color(palette.onSurfaceMuted),
            error = Color(palette.danger),
        )
    }
    MaterialTheme(colorScheme = scheme) {
        Surface(modifier = Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) { content() }
    }
}

@Composable
private fun PythiaScreen(connection: PythiaConnection) {
    val state by connection.state.collectAsState()
    val events by connection.events.collectAsState()
    // The window's own log, which CI reads: what has been composed on screen so far.
    LaunchedEffect(events.size) {
        if (events.isNotEmpty()) uiLog("ui: rendered events=${events.size} newest=${events.first().name}")
    }
    Column(
        modifier = Modifier.fillMaxSize().padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("Pythia", style = MaterialTheme.typography.headlineMedium)
        when (val s = state) {
            is PythiaState.Connecting -> Text("Connecting to Pythia...")
            is PythiaState.Unavailable -> {
                LaunchedEffect(s.reason) { uiLog("ui: rendered unavailable: ${s.reason}") }
                Card(
                    modifier = Modifier.fillMaxWidth(),
                    colors = CardDefaults.cardColors(containerColor = Color(ThemePalette.DEFAULT.dangerPlate)),
                ) {
                    Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Text("Pythia is not available", style = MaterialTheme.typography.titleMedium)
                        Text(s.reason)
                        Button(onClick = { connection.retryNow() }) { Text("Try again") }
                    }
                }
            }
            is PythiaState.Ready -> {
                AnswerCard("hello", s.hello)
                val info = s.systemInfo
                if (info == null) Text("Asking for the system info...") else AnswerCard("get_system_info", info)
                s.games?.let { GamesCard(it) }
            }
        }
        Text(
            "Events (${ValueFormat.count(events.size.toLong())})",
            style = MaterialTheme.typography.titleMedium,
        )
        LazyColumn(modifier = Modifier.fillMaxWidth().weight(1f)) {
            items(events, key = { it.number }) { e ->
                Row(Modifier.fillMaxWidth().padding(vertical = 2.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("#${e.number}", color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Text(e.name, color = MaterialTheme.colorScheme.primary)
                    Text(e.args, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1)
                }
            }
        }
    }
}

/** The typed `get_games`: how many games Pythia has, read as the schema's `GetGamesResult`, or why it could not be read. */
@Composable
private fun GamesCard(games: TypedAnswer<GetGamesResult>) {
    val line = games.summary { "${ValueFormat.count(it.games.size.toLong())} games, version ${it.version}" }
    LaunchedEffect(line) { uiLog("ui: rendered get_games typed $line") }
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text("get_games", style = MaterialTheme.typography.titleMedium)
            Text(line, color = if (games is TypedAnswer.Ok<*>) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.error)
        }
    }
}

/** One answer of the backend, as it came: its status and its fields, or the reason it is not ok. */
@Composable
private fun AnswerCard(title: String, answer: Answer) {
    LaunchedEffect(answer) { uiLog("ui: rendered $title status=${if (answer is Answer.Ok) "ok" else "not ok"}") }
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text(title, style = MaterialTheme.typography.titleMedium)
            when (answer) {
                is Answer.Ok -> fieldRows(answer.fields)
                is Answer.Unavailable -> Text("Unavailable: ${answer.message}", color = MaterialTheme.colorScheme.error)
                is Answer.Error -> Text("Error: ${answer.message}", color = MaterialTheme.colorScheme.error)
                is Answer.Pending -> Text("Pending")
            }
        }
    }
}

@Composable
private fun fieldRows(fields: JsonObject) {
    for ((name, value) in fields) {
        val text = (value as? JsonPrimitive)?.content ?: value.toString()
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(name, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Text(text)
        }
    }
}
