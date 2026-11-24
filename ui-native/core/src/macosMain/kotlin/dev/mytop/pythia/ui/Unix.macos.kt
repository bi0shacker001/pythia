@file:OptIn(ExperimentalForeignApi::class)

package dev.mytop.pythia.ui

import kotlinx.cinterop.ByteVar
import kotlinx.cinterop.ExperimentalForeignApi
import kotlinx.cinterop.IntVar
import kotlinx.cinterop.alloc
import kotlinx.cinterop.allocArray
import kotlinx.cinterop.convert
import kotlinx.cinterop.memScoped
import kotlinx.cinterop.ptr
import kotlinx.cinterop.reinterpret
import kotlinx.cinterop.set
import kotlinx.cinterop.value
import platform.posix.AF_UNIX
import platform.posix.SOCK_STREAM
import platform.posix.SOL_SOCKET
import platform.posix.SO_NOSIGPIPE
import platform.posix.connect
import platform.posix.setsockopt
import platform.posix.socket

// macOS has no MSG_NOSIGNAL; the socket option SO_NOSIGPIPE does the same for the whole socket.
internal val sendFlags: Int = 0

// sockaddr_un is not in Kotlin/Native's macOS bindings, so it is laid out by hand as <sys/un.h> has it:
// sun_len (1 byte), sun_family (1 byte), sun_path (104 bytes), 106 in all.
private const val SOCKADDR_UN_SIZE = 106
private const val SUN_PATH_OFFSET = 2

internal fun connectUnixSocket(address: String): Int = memScoped {
    val bytes = address.encodeToByteArray()
    val addr = allocArray<ByteVar>(SOCKADDR_UN_SIZE)
    for (i in 0 until SOCKADDR_UN_SIZE) addr[i] = 0
    addr[0] = SOCKADDR_UN_SIZE.toByte()
    addr[1] = AF_UNIX.toByte()
    for (i in bytes.indices) addr[SUN_PATH_OFFSET + i] = bytes[i]
    val fd = socket(AF_UNIX, SOCK_STREAM.convert(), 0)
    if (fd < 0) throw StreamException(errorText())
    val one = alloc<IntVar>()
    one.value = 1
    setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, one.ptr, 4u)
    if (connect(fd, addr.reinterpret(), SOCKADDR_UN_SIZE.convert()) != 0) {
        val reason = errorText()
        platform.posix.close(fd)
        throw StreamException(reason)
    }
    fd
}
