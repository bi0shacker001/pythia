package dev.mytop.pythia.ui

import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext

/** One offer owner at a time, including its withdrawal. Capture the connection only after prior cleanup ends. */
internal class PairingLifecycle<T>(private val current: () -> T?) {
    private val lock = Mutex()

    suspend fun <R> run(
        start: suspend (T) -> R,
        stop: suspend (T) -> Unit,
        body: suspend (T, R) -> Unit,
    ): Boolean = lock.withLock {
        val session = current() ?: return@withLock false
        try {
            // Starting an offer mutates the backend even if the caller leaves. Wait for its answer before withdrawing
            // it, otherwise an asynchronous start could finish after Stop and leave an invisible offer behind.
            val initial = withContext(NonCancellable) { start(session) }
            currentCoroutineContext().ensureActive()
            body(session, initial)
        } finally {
            withContext(NonCancellable) { stop(session) }
        }
        true
    }
}
