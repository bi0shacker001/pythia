package dev.mytop.pythia.ui

import dev.droidtop.lib.ui.contract.Answer
import dev.mytop.pythia.ui.backend.PythiaBackend
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
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
