@file:OptIn(ExperimentalForeignApi::class)

package dev.mytop.pythia.ui

import kotlinx.cinterop.ByteVar
import kotlinx.cinterop.ExperimentalForeignApi
import kotlinx.cinterop.addressOf
import kotlinx.cinterop.allocArray
import kotlinx.cinterop.convert
import kotlinx.cinterop.memScoped
import kotlinx.cinterop.readBytes
import kotlinx.cinterop.toKString
import kotlinx.cinterop.usePinned
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.IO
import platform.posix.EINTR
import platform.posix.SHUT_RDWR
import platform.posix.errno
import platform.posix.fclose
import platform.posix.fflush
import platform.posix.fopen
import platform.posix.fread
import platform.posix.getenv
import platform.posix.send
import platform.posix.shutdown
import platform.posix.strerror

// Compiled into linuxMain and macosMain alike. Each has its own `sendFlags` and `connectUnixSocket` (a connected stream
// socket for an address, or a StreamException with the reason); the rest is the same.

actual val blockingDispatcher: CoroutineDispatcher = Dispatchers.IO

actual val isWindowsHost: Boolean = false

actual fun environmentVariable(name: String): String? = getenv(name)?.toKString()

actual fun flushOutput() {
    fflush(null)
}

actual fun readTextFile(path: String): String? {
    val file = fopen(path, "r") ?: return null
    var bytes = ByteArray(0)
    try {
        memScoped {
            val buf = allocArray<ByteVar>(4096)
            while (true) {
                val n = fread(buf, 1u, 4096u, file).toInt()
                if (n <= 0) break
                bytes += buf.readBytes(n)
            }
        }
    } finally {
        fclose(file)
    }
    return bytes.decodeToString()
}

internal fun errorText(): String = strerror(errno)?.toKString() ?: "error $errno"

/** A Unix stream socket; sockets are full duplex, so one thread reads while another writes. */
private class UnixStream(private val fd: Int) : Stream {
    override fun read(buffer: ByteArray): Int {
        while (true) {
            val n = buffer.usePinned { platform.posix.read(fd, it.addressOf(0), buffer.size.convert()) }.toLong()
            if (n > 0) return n.toInt()
            if (n == 0L) return -1
            if (errno == EINTR) continue
            throw StreamException(errorText())
        }
    }

    override fun write(data: ByteArray) {
        var sent = 0
        while (sent < data.size) {
            val n = data.usePinned {
                send(fd, it.addressOf(sent), (data.size - sent).convert(), sendFlags)
            }.toLong()
            if (n < 0) {
                if (errno == EINTR) continue
                throw StreamException(errorText())
            }
            sent += n.toInt()
        }
    }

    override fun close() {
        shutdown(fd, SHUT_RDWR.convert())
        platform.posix.close(fd)
    }
}

actual fun openStream(address: String): Stream {
    if (address.encodeToByteArray().size >= 100) throw StreamException("the socket path is too long for a Unix socket")
    return UnixStream(connectUnixSocket(address))
}
