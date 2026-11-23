package dev.mytop.pythia.ui

import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith

class FramingTest {
    private val hello = buildJsonObject {
        put("id", 1)
        put("method", "hello")
        put("text", "caf\u00e9")
    }

    @Test
    fun aFrameIsFourBigEndianBytesOfLengthThenUtf8Json() {
        val bytes = Framing.encode(hello)
        val length = ((bytes[0].toInt() and 0xFF) shl 24) or ((bytes[1].toInt() and 0xFF) shl 16) or
            ((bytes[2].toInt() and 0xFF) shl 8) or (bytes[3].toInt() and 0xFF)
        assertEquals(bytes.size - 4, length)
        assertEquals(hello.toString(), bytes.decodeToString(4, bytes.size))
    }

    @Test
    fun theDecoderGivesBackWholeFramesFromAnyChunks() {
        val two = Framing.encode(hello) + Framing.encode(buildJsonObject { put("event", "gamesChanged") })
        val decoder = FrameDecoder()
        val got = ArrayList<JsonObject>()
        for (b in two) got += decoder.feed(byteArrayOf(b))
        assertEquals(2, got.size)
        assertEquals(JsonPrimitive("hello"), got[0]["method"])
        assertEquals(JsonPrimitive("gamesChanged"), got[1]["event"])
        assertEquals(0, decoder.pending)
    }

    @Test
    fun aHalfFrameWaits() {
        val bytes = Framing.encode(hello)
        val decoder = FrameDecoder()
        assertEquals(0, decoder.feed(bytes, bytes.size - 1).size)
        assertEquals(bytes.size - 1, decoder.pending)
    }

    @Test
    fun aHostileLengthIsRefused() {
        assertFailsWith<FrameException> { FrameDecoder().feed(byteArrayOf(0x7F, 0, 0, 0, 1)) }
        assertFailsWith<FrameException> { FrameDecoder().feed(byteArrayOf(0, 0, 0, 0)) }
    }

    @Test
    fun aFrameThatIsNotAnObjectIsRefused() {
        val body = "[1]".encodeToByteArray()
        assertFailsWith<FrameException> { FrameDecoder().feed(byteArrayOf(0, 0, 0, body.size.toByte()) + body) }
    }
}
