package dev.mytop.pythia.ui

import dev.droidtop.lib.ui.contract.Answer
import dev.mytop.pythia.ui.backend.PythiaBackend
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.withTimeout
import kotlinx.serialization.json.JsonObject
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertFailsWith
import kotlin.test.assertIs

class BackendCancellationTest {
    private class SilentStream(val written: CompletableDeferred<Unit> = CompletableDeferred()) : Stream {
        override fun read(buffer: ByteArray): Int = -1
        override fun write(data: ByteArray) { written.complete(Unit) }
        override fun close() = Unit
    }

    @Test
    fun leavingTheCallerDoesNotReturnAnOrdinaryFailureResult() = runTest {
        val stream = SilentStream()
        val channel = BackendChannel(stream, this)
        var resumedNormally = false
        val request = launch {
            channel.call("waiting", JsonObject(emptyMap()))
            resumedNormally = true
        }
        stream.written.await()
        request.cancelAndJoin()
        assertFalse(resumedNormally)
    }

    @Test
    fun anOuterDeadlineKeepsItsCancellationSemantics() = runTest {
        val channel = BackendChannel(SilentStream(), this)
        assertFailsWith<TimeoutCancellationException> {
            withTimeout(25) { channel.call("waiting", JsonObject(emptyMap()), timeoutMs = 60_000) }
        }
    }

    @Test
    fun theChannelsOwnDeadlineStillReturnsAUsefulAnswer() = runTest {
        val channel = BackendChannel(SilentStream(), this)
        val answer = channel.call("waiting", JsonObject(emptyMap()), timeoutMs = 25)
        assertEquals("timeout", assertIs<Answer.Error>(answer).code)
    }

    @Test
    fun aCapturedPairingSessionCannotCallAfterItsConnectionEnds() = runTest {
        var current = true
        var calls = 0
        val session = PythiaSession(PythiaBackend(BackendChannel(SilentStream(), this))) { current }
        session.call { calls++; Answer.Ok(JsonObject(emptyMap())) }
        current = false
        val answer = session.call { calls++; Answer.Ok(JsonObject(emptyMap())) }
        assertIs<Answer.Unavailable>(answer)
        assertEquals(1, calls)
    }
}
