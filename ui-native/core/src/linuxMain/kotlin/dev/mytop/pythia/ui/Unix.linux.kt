@file:OptIn(ExperimentalForeignApi::class)

package dev.mytop.pythia.ui

import kotlinx.cinterop.ExperimentalForeignApi
import kotlinx.cinterop.addressOf
import kotlinx.cinterop.alloc
import kotlinx.cinterop.convert
import kotlinx.cinterop.memScoped
import kotlinx.cinterop.ptr
import kotlinx.cinterop.reinterpret
import kotlinx.cinterop.sizeOf
import kotlinx.cinterop.usePinned
import platform.linux.sockaddr_un
import platform.posix.AF_UNIX
import platform.posix.MSG_NOSIGNAL
import platform.posix.SOCK_STREAM
import platform.posix.connect
import platform.posix.memcpy
import platform.posix.memset
import platform.posix.socket

internal val sendFlags: Int = MSG_NOSIGNAL.convert()

internal fun connectUnixSocket(address: String): Int = memScoped {
    val bytes = address.encodeToByteArray()
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
    fd
}
