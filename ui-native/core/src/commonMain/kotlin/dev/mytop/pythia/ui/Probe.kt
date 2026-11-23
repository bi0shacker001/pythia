package dev.mytop.pythia.ui

import dev.droidtop.lib.ui.contract.Answer
import dev.mytop.pythia.ui.backend.PythiaBackend
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeoutOrNull

/**
 * `--probe`: connects to the running Pythia the way the window does, calls `hello` and `get_system_info`, prints the
 * answers and returns the exit status: 0 when both are ok, 1 when they are not, 2 when Pythia could not be reached.
 */
fun probe(out: (String) -> Unit = ::println): Int = runBlocking {
    val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    val outcome = withTimeoutOrNull(60_000) {
        when (val opened = BackendChannel.open(scope)) {
            is Opened.Down -> {
                out("unavailable: ${opened.reason}")
                2
            }
            is Opened.Up -> {
                out("hello: ${opened.hello.encode()}")
                val info = PythiaBackend(opened.channel).getSystemInfo()
                out("get_system_info: ${info.encode()}")
                opened.channel.close()
                if (info is Answer.Ok) 0 else 1
            }
        }
    }
    outcome ?: run {
        out("unavailable: Pythia did not answer within 60 seconds.")
        2
    }
}

/**
 * `--watch`: keeps the connection a window keeps, and logs every change of state (`state: ready`, `state: unavailable: why`).
 * Returns 0 once Pythia has been seen ready, then gone, then ready again, which is what a Pythia that restarts looks
 * like to the window; 2 when that does not happen within [limitMs].
 */
fun watch(limitMs: Long = 240_000, log: (String) -> Unit = ::uiLog): Int = runBlocking {
    val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    val connection = PythiaConnection(scope)
    connection.start()
    val outcome = withTimeoutOrNull(limitMs) {
        var stage = 0 // 0: waiting for ready, 1: ready seen, 2: gone seen
        var last: String? = null
        while (true) {
            val state = connection.state.first { describe(it) != last }
            last = describe(state)
            log("state: $last")
            when {
                stage == 0 && state is PythiaState.Ready -> stage = 1
                stage == 1 && state is PythiaState.Unavailable -> stage = 2
                stage == 2 && state is PythiaState.Ready -> return@withTimeoutOrNull 0
            }
        }
        @Suppress("UNREACHABLE_CODE")
        2
    }
    outcome ?: run {
        log("watch: the sequence ready, gone, ready did not happen in ${limitMs / 1000} seconds")
        2
    }
}

private fun describe(state: PythiaState): String = when (state) {
    is PythiaState.Connecting -> "connecting"
    is PythiaState.Ready -> "ready"
    is PythiaState.Unavailable -> "unavailable: ${state.reason}"
}
