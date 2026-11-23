@file:OptIn(ExperimentalForeignApi::class)

package dev.mytop.pythia.ui

import kotlinx.cinterop.ByteVar
import kotlinx.cinterop.readBytes
import kotlinx.cinterop.ExperimentalForeignApi
import kotlinx.cinterop.addressOf
import kotlinx.cinterop.alloc
import kotlinx.cinterop.allocArray
import kotlinx.cinterop.convert
import kotlinx.cinterop.memScoped
import kotlinx.cinterop.ptr
import kotlinx.cinterop.reinterpret
import kotlinx.cinterop.sizeOf
import kotlinx.cinterop.toKString
import kotlinx.cinterop.usePinned
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.IO
import platform.posix.AF_UNIX
import platform.posix.EINTR
import platform.posix.MSG_NOSIGNAL
import platform.posix.SHUT_RDWR
import platform.posix.SOCK_STREAM
import platform.posix.connect
import platform.posix.errno
import platform.posix.fclose
import platform.posix.fopen
import platform.posix.fread
import platform.posix.getenv
import platform.posix.memcpy
import platform.posix.memset
import platform.posix.send
import platform.posix.shutdown
import platform.posix.sockaddr_un
import platform.posix.socket
import platform.posix.strerror

actual val blockingDispatcher: CoroutineDispatcher = Dispatchers.IO

actual val isWindowsHost: Boolean = false

actual fun environmentVariable(name: String): String? = getenv(name)?.toKString()

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

private fun errorText(): String = strerror(errno)?.toKString() ?: "error $errno"

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
                // MSG_NOSIGNAL: a Pythia that has gone is an error here, not a SIGPIPE that ends this program.
                send(fd, it.addressOf(sent), (data.size - sent).convert(), MSG_NOSIGNAL.convert())
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

actual fun openStream(address: String): Stream = memScoped {
    val bytes = address.encodeToByteArray()
    if (bytes.size >= 100) throw StreamException("the socket path is too long for a Unix socket")
    val addr = alloc<sockaddr_un>()
    val fd = socket(AF_UNIX, SOCK_STREAM.convert(), 0)
    if (fd < 0) throw StreamException(errorText())
    memset(addr.ptr, 0, sizeOf<sockaddr_un>().convert())
    addr.sun_family = AF_UNIX.convert()
    bytes.usePinned { memcpy(addr.sun_path, it.addressOf(0), bytes.size.convert()) }
    if (connect(fd, addr.ptr.reinterpret(), sizeOf<sockaddr_un>().convert()) != 0) {
        val reason = errorText()
        platform.posix.close(fd)
        throw StreamException(reason)
    }
    UnixStream(fd)
}
