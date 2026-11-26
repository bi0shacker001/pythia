package dev.mytop.pythia.ui

import dev.droidtop.lib.ui.contract.Answer
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.mapNotNull
import kotlinx.serialization.KSerializer
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray

/**
 * How the backend's answers and event payloads are read: a key the schema does not name is ignored, so a newer Pythia
 * that adds a field does not break an older front end. (A key the schema marks optional, or that may be null, has a
 * default in its generated class, so it may be missing or null.)
 */
val BackendJson: Json = Json { ignoreUnknownKeys = true }

/**
 * An answer of a method the schema types: [Ok] with the fields read as the result type, or [Other], the contract
 * [Answer] as it came when it is unavailable, an error or pending, or when it is ok but does not read as the type (then it
 * is an [Answer.Error] with the code `malformed`, contract rule 1: an answer that cannot be read is an error).
 */
sealed interface TypedAnswer<out T> {
    /** The ok answer: its fields as [value], and the answer itself for anything beside them. */
    data class Ok<T>(val value: T, val answer: Answer.Ok) : TypedAnswer<T>

    data class Other(val answer: Answer) : TypedAnswer<Nothing>
}

/** Reads this answer's fields as [serializer]'s type. */
fun <T> Answer.typed(serializer: KSerializer<T>): TypedAnswer<T> {
    if (this !is Answer.Ok) return TypedAnswer.Other(this)
    return try {
        TypedAnswer.Ok(BackendJson.decodeFromJsonElement(serializer, fields), this)
    } catch (e: IllegalArgumentException) { // SerializationException is one
        TypedAnswer.Other(
            Answer.Error(
                "The answer of ${slot ?: "the backend"} is not in the shape the schema says: ${e.message}",
                code = "malformed",
                slot = slot,
            ),
        )
    }
}

/** The payload of each event of a flow of an event's arguments, read as [serializer]'s type; one that does not read is skipped. */
fun <T> Flow<JsonArray>.typedEvents(serializer: KSerializer<T>): Flow<T> = mapNotNull { args ->
    val first = args.firstOrNull() ?: return@mapNotNull null
    try {
        BackendJson.decodeFromJsonElement(serializer, first)
    } catch (e: IllegalArgumentException) {
        null
    }
}

/** One line for a screen or a log: what a typed answer came to. */
fun <T> TypedAnswer<T>.summary(ok: (T) -> String): String = when (this) {
    is TypedAnswer.Ok -> "ok ${ok(value)}"
    is TypedAnswer.Other -> when (val a = answer) {
        is Answer.Ok -> "ok"
        is Answer.Unavailable -> "unavailable: ${a.message}"
        is Answer.Error -> "error: ${a.message}"
        is Answer.Pending -> "pending"
    }
}

/** True for the one outcome that means the client and the schema disagree: an ok answer that did not read as its type. */
fun <T> TypedAnswer<T>.isMalformed(): Boolean = this is TypedAnswer.Other && (answer as? Answer.Error)?.code == "malformed"
