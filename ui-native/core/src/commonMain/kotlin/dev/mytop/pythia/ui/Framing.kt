package dev.mytop.pythia.ui

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject

/** Nothing Pythia sends is near this; a bigger length is a corrupt or hostile stream (pythia/ipc/framing.py). */
const val MAX_FRAME = 16 * 1024 * 1024

class FrameException(message: String) : Exception(message)

/** The wire format of the backend channel: a 4-byte big-endian length, then that many bytes of UTF-8 JSON (an object). */
object Framing {
    fun encode(message: JsonObject): ByteArray {
        val body = Json.encodeToString(JsonElement.serializer(), message).encodeToByteArray()
        if (body.size > MAX_FRAME) throw FrameException("A message of ${body.size} bytes is over the $MAX_FRAME byte limit.")
        val out = ByteArray(4 + body.size)
        out[0] = (body.size ushr 24).toByte()
        out[1] = (body.size ushr 16).toByte()
        out[2] = (body.size ushr 8).toByte()
        out[3] = body.size.toByte()
        body.copyInto(out, 4)
        return out
    }
}

/** Collects bytes in any chunks and hands back each complete message, in order. */
class FrameDecoder(private val maxFrame: Int = MAX_FRAME) {
    private var buffer = ByteArray(0)

    /** Bytes held that do not make a whole frame yet. */
    val pending: Int get() = buffer.size

    fun feed(data: ByteArray, length: Int = data.size): List<JsonObject> {
        buffer += data.copyOfRange(0, length)
        val messages = ArrayList<JsonObject>()
        var start = 0
        while (buffer.size - start >= 4) {
            val size = ((buffer[start].toInt() and 0xFF) shl 24) or
                ((buffer[start + 1].toInt() and 0xFF) shl 16) or
                ((buffer[start + 2].toInt() and 0xFF) shl 8) or
                (buffer[start + 3].toInt() and 0xFF)
            if (size <= 0 || size > maxFrame) throw FrameException("A frame of $size bytes is not allowed.")
            val end = start + 4 + size
            if (buffer.size < end) break
            val text = buffer.decodeToString(start + 4, end)
            val element = try {
                Json.parseToJsonElement(text)
            } catch (e: Exception) {
                throw FrameException("A frame is not JSON: ${e.message}")
            }
            messages += element as? JsonObject ?: throw FrameException("A frame is not a JSON object.")
            start = end
        }
        if (start > 0) buffer = buffer.copyOfRange(start, buffer.size)
        return messages
    }
}
