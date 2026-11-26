package dev.mytop.pythia.ui

import dev.droidtop.lib.ui.contract.Answer
import dev.mytop.pythia.ui.backend.GetGamesResult
import dev.mytop.pythia.ui.backend.PythiaBackend
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Job
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull

/** What the front end knows of Pythia right now. */
sealed interface PythiaState {
    data object Connecting : PythiaState

    /** Connected: Pythia's `hello`, `get_system_info` once it has answered, and `get_games`, a typed answer, after it. */
    data class Ready(val hello: Answer, val systemInfo: Answer?, val games: TypedAnswer<GetGamesResult>? = null) : PythiaState

    /** Not connected; [reason] says why in words a screen can show. */
    data class Unavailable(val reason: String) : PythiaState
}

/** One line of the live event stream. */
data class LoggedEvent(val number: Long, val name: String, val args: String)

/**
 * Keeps a connection to the running Pythia: connects, asks `hello` and `get_system_info`, logs every event, and when
 * Pythia is not there (or goes away) says why and tries again every few seconds. Nothing is invented while it is down.
 */
class PythiaConnection(
    private val scope: CoroutineScope,
    private val where: Locations = hostLocations(),
    private val retryEveryMs: Long = 3_000,
) {
    private val _state = MutableStateFlow<PythiaState>(PythiaState.Connecting)
    val state: StateFlow<PythiaState> get() = _state

    private val _events = MutableStateFlow<List<LoggedEvent>>(emptyList())

    /** The newest events first, the last [MAX_EVENTS] of them. */
    val events: StateFlow<List<LoggedEvent>> get() = _events

    private val retry = Channel<Unit>(Channel.CONFLATED)
    private var counter = 0L

    fun start(): Job = scope.launch { run() }

    /** Tries again now rather than at the next interval. */
    fun retryNow() {
        retry.trySend(Unit)
    }

    private suspend fun run() {
        while (true) {
            _state.value = PythiaState.Connecting
            val ended = CompletableDeferred<String>()
            when (val opened = BackendChannel.open(scope, where) { ended.complete(it) }) {
                is Opened.Down -> _state.value = PythiaState.Unavailable(opened.reason)
                is Opened.Up -> {
                    val backend = PythiaBackend(opened.channel)
                    val collecting = scope.launch(start = CoroutineStart.UNDISPATCHED) {
                        backend.events.collect { e ->
                            _events.update { listOf(LoggedEvent(++counter, e.name, e.args.toString())) + it.take(MAX_EVENTS - 1) }
                        }
                    }
                    _state.value = PythiaState.Ready(opened.hello, null)
                    val info = backend.getSystemInfo()
                    _state.value = PythiaState.Ready(opened.hello, info)
                    _state.value = PythiaState.Ready(opened.hello, info, backend.getGames())
                    _state.value = PythiaState.Unavailable(ended.await())
                    collecting.cancel()
                    opened.channel.close()
                }
            }
            withTimeoutOrNull(retryEveryMs) { retry.receive() }
        }
    }

    companion object {
        const val MAX_EVENTS = 500
    }
}
