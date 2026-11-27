package dev.mytop.pythia.ui

import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.awaitCancellation
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse

@OptIn(ExperimentalCoroutinesApi::class)
class PairingLifecycleTest {
    @Test
    fun replacementWaitsForWithdrawalAndCleanupUsesOriginalConnection() = runTest {
        var connection = "old"
        val lifecycle = PairingLifecycle { connection }
        val calls = mutableListOf<String>()
        val oldStarted = CompletableDeferred<Unit>()
        val oldStopping = CompletableDeferred<Unit>()
        val releaseStop = CompletableDeferred<Unit>()
        val newStarted = CompletableDeferred<Unit>()
        val old = launch {
            lifecycle.run(start = { calls += "start:$it"; oldStarted.complete(Unit) }, stop = {
                calls += "stop:$it"
                oldStopping.complete(Unit)
                releaseStop.await()
            }) { _, _ -> awaitCancellation() }
        }
        oldStarted.await()
        old.cancel()
        oldStopping.await()
        connection = "new"
        val next = launch {
            lifecycle.run(start = { calls += "start:$it"; newStarted.complete(Unit) },
                stop = { calls += "stop:$it" }) { _, _ -> awaitCancellation() }
        }
        runCurrent()
        assertFalse(newStarted.isCompleted)
        releaseStop.complete(Unit)
        old.join()
        newStarted.await()
        next.cancelAndJoin()
        assertEquals(listOf("start:old", "stop:old", "start:new", "stop:new"), calls)
    }

    @Test
    fun leavingDuringStartupWithdrawsOnlyAfterTheStartSettles() = runTest {
        val lifecycle = PairingLifecycle { "connection" }
        val entered = CompletableDeferred<Unit>()
        val finishStart = CompletableDeferred<Unit>()
        val calls = mutableListOf<String>()
        val offer = launch {
            lifecycle.run(start = {
                entered.complete(Unit)
                finishStart.await()
                calls += "started"
            }, stop = { calls += "stopped" }) { _, _ -> calls += "polled" }
        }
        entered.await()
        offer.cancel()
        runCurrent()
        assertEquals(emptyList(), calls)
        finishStart.complete(Unit)
        offer.join()
        assertEquals(listOf("started", "stopped"), calls)
    }

    @Test
    fun cancellingAQueuedOwnerDoesNotWithdrawSomeoneElsesOffer() = runTest {
        val lifecycle = PairingLifecycle { "connection" }
        val started = CompletableDeferred<Unit>()
        val calls = mutableListOf<String>()
        val owner = launch {
            lifecycle.run(start = { started.complete(Unit) }, stop = { calls += "owner stopped" }) { _, _ -> awaitCancellation() }
        }
        started.await()
        val queued = launch {
            lifecycle.run(start = { calls += "queued started" }, stop = { calls += "queued stopped" }) { _, _ -> }
        }
        runCurrent()
        queued.cancelAndJoin()
        assertEquals(emptyList(), calls)
        owner.cancelAndJoin()
        assertEquals(listOf("owner stopped"), calls)
    }
}
