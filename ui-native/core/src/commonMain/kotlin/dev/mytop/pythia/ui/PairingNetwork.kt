package dev.mytop.pythia.ui

import dev.droidtop.lib.ui.contract.Answer
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull

/** Existing desktop copy, required before the first pairing listener (SPEC 7.5). */
const val PAIRING_NETWORK_EXPLANATION = "To pair, Pythia talks to the handheld or the other computer over your home network: it listens on TCP and UDP port 47610 and TCP 47612 while you pair. Your system may ask whether Pythia may use the network: allow it on private networks."

sealed interface PairingNetworkDecision {
    data object Ready : PairingNetworkDecision
    data object Explain : PairingNetworkDecision
    data class Blocked(val answer: Answer) : PairingNetworkDecision
}

/** An unavailable or malformed read cannot silently bypass the first-use explanation. */
fun pairingNetworkDecision(answer: Answer): PairingNetworkDecision {
    if (answer !is Answer.Ok) return PairingNetworkDecision.Blocked(answer)
    return when ((answer.fields["explained"] as? JsonPrimitive)?.booleanOrNull) {
        true -> PairingNetworkDecision.Ready
        false -> PairingNetworkDecision.Explain
        null -> PairingNetworkDecision.Blocked(Answer.Error("Pythia did not report whether the pairing network explanation was shown."))
    }
}
