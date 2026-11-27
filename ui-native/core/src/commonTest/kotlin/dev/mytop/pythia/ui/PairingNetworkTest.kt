package dev.mytop.pythia.ui

import dev.droidtop.lib.ui.contract.Answer
import kotlinx.serialization.json.jsonObject
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertIs

class PairingNetworkTest {
    private fun answer(text: String) = Answer.Ok(BackendJson.parseToJsonElement(text).jsonObject)

    @Test
    fun onlyARecordedExplanationCanSkipConfirmation() {
        assertEquals(PairingNetworkDecision.Ready, pairingNetworkDecision(answer("""{"explained":true}""")))
        assertEquals(PairingNetworkDecision.Explain, pairingNetworkDecision(answer("""{"explained":false}""")))
        assertIs<PairingNetworkDecision.Blocked>(pairingNetworkDecision(answer("{}")))
        assertIs<PairingNetworkDecision.Blocked>(pairingNetworkDecision(Answer.Unavailable("Disconnected")))
        assertIs<PairingNetworkDecision.Blocked>(pairingNetworkDecision(Answer.Error("Read failed")))
    }
}
