package dev.mytop.pythia.ui

import dev.droidtop.lib.ui.contract.Answer
import dev.mytop.pythia.ui.backend.BackendSamples
import dev.mytop.pythia.ui.backend.Card
import dev.mytop.pythia.ui.backend.GameStopped
import dev.mytop.pythia.ui.backend.GetGamesResult
import dev.mytop.pythia.ui.backend.GetRunningGamesResult
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.flow.toList
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.put
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertIs
import kotlin.test.assertNotNull
import kotlin.test.assertNull
import kotlin.test.assertTrue

/**
 * The typed client against the real schema: [BackendSamples] is made by the build from schema/backend-ipc.json (a full and
 * a sparse sample of every typed method's result and every typed event's payload, each with a key the schema does not
 * name), so these tests fail when the generated types stop reading what the schema describes.
 */
class TypedAnswersTest {
    private fun parse(text: String): JsonObject = BackendJson.parseToJsonElement(text).jsonObject

    private fun <T> TypedAnswer<T>.okValue(): T = when (this) {
        is TypedAnswer.Ok -> value
        is TypedAnswer.Other -> error("not ok: $answer")
    }

    @Test
    fun everyTypedMethodReadsItsFullAndItsSparseSample() {
        assertTrue(BackendSamples.answers.isNotEmpty(), "the schema types no method")
        for (sample in BackendSamples.answers) {
            assertEquals(2, sample.texts.size)
            for (text in sample.texts) {
                val value = sample.decode(parse(text))
                assertNotNull(value, "${sample.name} did not read: $text")
            }
        }
    }

    @Test
    fun everyTypedEventReadsItsFullAndItsSparseSample() {
        assertTrue(BackendSamples.events.isNotEmpty(), "the schema types no event")
        for (sample in BackendSamples.events) {
            for (text in sample.texts) assertNotNull(sample.decode(parse(text)), "${sample.name} did not read: $text")
        }
    }

    @Test
    fun anOkAnswerIsReadAsItsTypeThroughTheAnswersOwnFields() {
        val sample = BackendSamples.answers.first { it.name == "get_games" }
        val answer = Answer.Ok(fields = parse(sample.texts[0]), slot = "get_games")
        val typed = answer.typed(GetGamesResult.serializer())
        val value = typed.okValue()
        assertEquals(1, value.games.size)
        assertEquals("sample", value.version)
    }

    @Test
    fun aRealisticGamesAnswerIsReadWithItsNullsAndItsOptionalKeys() {
        val fields = parse(
            """{"version":"v1","unchanged":false,"games":[{"id":"g1","title":"Moonlit Harbor","sort_name":"moonlit harbor",
            "platforms":["Linux"],"systems":["linux"],"runner":null,"runners":[],"installed":true,"last_played":null,"playtime_hours":1.5,
            "playcount":0,"favourite":false,"art":["cover"],"year":2019,"added":null,"stores":["steam"],"sources":["folder"],
            "has_update":false,"a_key_a_later_pythia_adds":true}]}""",
        )
        val typed = Answer.Ok(fields = fields).typed(GetGamesResult.serializer())
        val card: Card = typed.okValue().games.single()
        assertEquals("Moonlit Harbor", card.title)
        assertNull(card.runner)
        assertNull(card.lastPlayed)
        assertEquals(1.5, card.playtimeHours)
        assertEquals(listOf("cover"), card.art)
    }

    @Test
    fun anAnswerThatIsNotInTheShapeTheSchemaSaysIsAMalformedError() {
        val missingKey = parse("""{"version":"v1","unchanged":false}""") // no games
        val typed = Answer.Ok(fields = missingKey, slot = "get_games").typed(GetGamesResult.serializer())
        assertIs<TypedAnswer.Other>(typed)
        val failure = typed.answer
        assertIs<Answer.Error>(failure)
        assertEquals("malformed", failure.code)
        assertEquals("get_games", failure.slot)
        assertTrue(typed.isMalformed())
    }

    @Test
    fun anAnswerThatIsNotOkPassesThroughAsItCame() {
        val unavailable = Answer.Unavailable("Pythia keeps no screenshots.", slot = "get_games")
        val typed = unavailable.typed(GetGamesResult.serializer())
        assertEquals(TypedAnswer.Other(unavailable), typed)
        assertFalse(typed.isMalformed())
        assertEquals("unavailable: Pythia keeps no screenshots.", typed.summary { "" })
        val pending = Answer.Pending("get_games-1")
        assertEquals(TypedAnswer.Other(pending), pending.typed(GetGamesResult.serializer()))
    }

    @Test
    fun anOkAnswerWithNoFieldsAtAllIsMalformedForATypeWithRequiredKeys() {
        val typed = Answer.Ok().typed(GetRunningGamesResult.serializer())
        assertTrue(typed.isMalformed())
    }

    @Test
    fun aTypedEventFlowReadsThePayloadAndSkipsWhatDoesNotRead() = runBlocking {
        val sample = BackendSamples.events.first { it.name == "gameStopped" }
        val good = JsonArray(listOf(parse(sample.texts[0])))
        val bad = JsonArray(listOf(buildJsonObject { put("game_id", 7) }))
        val empty = JsonArray(emptyList())
        val read = flowOf(good, bad, empty, good).typedEvents(GameStopped.serializer()).toList()
        assertEquals(2, read.size)
        assertEquals("sample", read[0].gameId)
    }
}
