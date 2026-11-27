package dev.mytop.pythia.ui

import dev.droidtop.lib.ui.contract.Answer
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.filter
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/** A signal of Pythia's Backend, pushed over the channel. */
data class BackendEvent(val name: String, val args: JsonArray)

/** What opening the channel came to. */
sealed interface Opened {
    class Up(val channel: BackendChannel, val hello: Answer) : Opened

    /** Not connected; [reason] is in words a screen can show. */
    class Down(val reason: String) : Opened
}

/**
 * One connection to Pythia's backend (pythia/ipc/server.py): length-prefixed JSON, `{id, method, args}` answered with
 * `{id, result}`, a pending answer settled later by `{id, settled, result}`, and `{event, args}` pushed. The
 * first message is `hello` with the install's key. Every call resolves to a contract [Answer]; a failure of the
 * channel is an [Answer.Error] (contract rule 1). Coroutine cancellation still propagates to the caller.
 */
class BackendChannel internal constructor(private val stream: Stream, private val scope: CoroutineScope) {
    private val lock = Mutex()
    private val writeLock = Mutex()
    private val waiting = HashMap<Long, CompletableDeferred<Answer>>()
    private var nextId = 1L
    private var closedReason: String? = null
    private val bus = MutableSharedFlow<BackendEvent>(extraBufferCapacity = 512, onBufferOverflow = BufferOverflow.DROP_OLDEST)

    /** Every event, as it arrives; collect it before the calls that should be seen. */
    val events: SharedFlow<BackendEvent> get() = bus

    /** The reason the channel ended, or null while it is up. */
    val closed: String? get() = closedReason

    fun eventsNamed(name: String): Flow<JsonArray> = bus.filter { it.name == name }.map { it.args }

    private fun startReading(onClosed: (String) -> Unit) {
        scope.launch(blockingDispatcher) {
            val decoder = FrameDecoder()
            val buffer = ByteArray(64 * 1024)
            var reason = "Pythia closed the connection."
            try {
                while (true) {
                    val n = stream.read(buffer)
                    if (n < 0) break
                    for (message in decoder.feed(buffer, n)) dispatch(message)
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                reason = e.message ?: "The connection to Pythia broke."
            } finally {
                withContext(NonCancellable) {
                    fail(reason)
                    onClosed(reason)
                }
            }
        }
    }

    private suspend fun dispatch(message: JsonObject) {
        val event = message["event"]
        if (event is JsonPrimitive && event.isString) {
            bus.tryEmit(BackendEvent(event.content, message["args"] as? JsonArray ?: JsonArray(emptyList())))
            return
        }
        val id = (message["id"] as? JsonPrimitive)?.content?.toLongOrNull() ?: return
        val answer = Answer.decode(message["result"])
        val settled = (message["settled"] as? JsonPrimitive)?.content == "true"
        // The first answer of a call that settles later is `pending`; the call goes on waiting for the settled one.
        if (answer is Answer.Pending && !settled) return
        lock.withLock { waiting.remove(id) }?.complete(answer)
    }

    private suspend fun fail(reason: String) {
        val all = lock.withLock {
            closedReason = closedReason ?: reason
            waiting.values.toList().also { waiting.clear() }
        }
        val error = Answer.Error(closedReason ?: reason, code = "closed")
        all.forEach { it.complete(error) }
    }

    /** Calls [method] with [args] (an object keyed by the schema's parameter names) and waits for its answer. */
    suspend fun call(method: String, args: JsonObject, timeoutMs: Long = 60_000): Answer {
        val deferred = CompletableDeferred<Answer>()
        val id = lock.withLock {
            closedReason?.let { return Answer.Error(it, code = "closed", slot = method) }
            nextId++.also { waiting[it] = deferred }
        }
        val frame = buildJsonObject {
            put("id", id)
            put("method", method)
            put("args", args)
        }
        try {
            val bytes = Framing.encode(frame)
            writeLock.withLock { withContext(blockingDispatcher) { stream.write(bytes) } }
            return withTimeoutOrNull(timeoutMs) { deferred.await() }
                ?: Answer.Error("Pythia did not answer $method in ${timeoutMs / 1000} seconds.", code = "timeout", slot = method)
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            return Answer.Error(e.message ?: "The call to Pythia failed.", code = "closed", slot = method)
        } finally {
            withContext(NonCancellable) { lock.withLock { waiting.remove(id) } }
        }
    }

    /** Ends the connection. */
    fun close() {
        runCatching { stream.close() }
    }

    companion object {
        /**
         * Opens the channel and says hello with the key from Pythia's data folder. [onClosed] is told once, with the
         * reason, when the connection ends. Connection failures are [Opened.Down.reason]; cancellation propagates.
         */
        suspend fun open(
            scope: CoroutineScope,
            where: Locations = hostLocations(),
            onClosed: (String) -> Unit = {},
        ): Opened {
            val keyFile = where.keyPath()
            val text = withContext(blockingDispatcher) { readTextFile(keyFile) }
                ?: return Opened.Down("Pythia has not run on this computer yet: there is no key at $keyFile.")
            val key = parseKey(text) ?: return Opened.Down("The key at $keyFile is not a key.")
            val address = where.address()
            val stream = try {
                withContext(blockingDispatcher) { openStream(address) }
            } catch (e: StreamException) {
                return Opened.Down("Pythia is not running: ${e.message} ($address).")
            }
            val channel = BackendChannel(stream, scope)
            channel.startReading(onClosed)
            val hello = try {
                channel.call("hello", buildJsonObject { put("token", key) }, timeoutMs = 15_000)
            } catch (e: CancellationException) {
                channel.close()
                throw e
            }
            return if (hello is Answer.Ok) {
                Opened.Up(channel, hello)
            } else {
                channel.close()
                Opened.Down(
                    when (hello) {
                        is Answer.Error -> hello.message
                        is Answer.Unavailable -> hello.message
                        else -> "Pythia did not accept the connection."
                    },
                )
            }
        }
    }
}
