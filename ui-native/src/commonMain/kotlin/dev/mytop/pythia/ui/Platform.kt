package dev.mytop.pythia.ui

/** A connected, blocking, full-duplex byte stream; one thread may read while another writes. */
interface Stream {
    /** Reads up to [buffer].size bytes; at least one, or -1 when the other side has gone. */
    fun read(buffer: ByteArray): Int

    /** Writes every byte. */
    fun write(data: ByteArray)

    /** Ends the stream; a read blocked on it returns -1 or throws. */
    fun close()
}

/** The channel could not be opened or broke; [message] says why in words a screen can show. */
class StreamException(message: String) : Exception(message)

/** True on Windows, where the channel is a named pipe. */
expect val isWindowsHost: Boolean

/** An environment variable, or null. */
expect fun environmentVariable(name: String): String?

/** A whole small file as text, or null when it cannot be read. */
expect fun readTextFile(path: String): String?

/** Opens the channel at [address]: a Unix socket path, or a named pipe on Windows. Throws [StreamException]. */
expect fun openStream(address: String): Stream

fun hostLocations(): Locations = Locations(isWindowsHost, ::environmentVariable)
