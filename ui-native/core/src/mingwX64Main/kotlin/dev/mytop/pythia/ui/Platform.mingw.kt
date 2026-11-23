@file:OptIn(ExperimentalForeignApi::class)

package dev.mytop.pythia.ui

import kotlinx.cinterop.ByteVar
import kotlinx.cinterop.readBytes
import kotlinx.cinterop.COpaquePointer
import kotlinx.cinterop.ExperimentalForeignApi
import kotlinx.cinterop.addressOf
import kotlinx.cinterop.alloc
import kotlinx.cinterop.allocArray
import kotlinx.cinterop.convert
import kotlinx.cinterop.memScoped
import kotlinx.cinterop.ptr
import kotlinx.cinterop.sizeOf
import kotlinx.cinterop.toKString
import kotlinx.cinterop.toLong
import kotlinx.cinterop.usePinned
import kotlinx.cinterop.value
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.IO
import platform.posix.fclose
import platform.posix.fflush
import platform.posix.fopen
import platform.posix.fread
import platform.posix.getenv
import platform.posix.memset
import platform.windows.CancelIoEx
import platform.windows.CloseHandle
import platform.windows.CreateEventA
import platform.windows.CreateFileA
import platform.windows.DWORDVar
import platform.windows.GetLastError
import platform.windows.GetOverlappedResult
import platform.windows.OVERLAPPED
import platform.windows.ReadFile
import platform.windows.Sleep
import platform.windows.WriteFile

actual val blockingDispatcher: CoroutineDispatcher = Dispatchers.IO

actual fun flushOutput() {
    fflush(null)
}

actual val isWindowsHost: Boolean = true

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

private const val ERROR_FILE_NOT_FOUND = 2
private const val ERROR_ACCESS_DENIED = 5
private const val ERROR_HANDLE_EOF = 38
private const val ERROR_BROKEN_PIPE = 109
private const val ERROR_PIPE_NOT_CONNECTED = 233
private const val ERROR_MORE_DATA = 234
private const val ERROR_PIPE_BUSY = 231
private const val ERROR_OPERATION_ABORTED = 995
private const val ERROR_IO_PENDING = 997

private const val GENERIC_READ_BIT = 0x80000000u
private const val GENERIC_WRITE_BIT = 0x40000000u
private const val OPEN_EXISTING_MODE = 3u
private const val FILE_FLAG_OVERLAPPED_BIT = 0x40000000u

private fun windowsError(code: Int): String = when (code) {
    ERROR_FILE_NOT_FOUND -> "no pipe of that name exists"
    ERROR_ACCESS_DENIED -> "access to the pipe was denied"
    ERROR_PIPE_BUSY -> "the pipe is busy"
    else -> "Windows error $code"
}

/**
 * The named pipe, opened for overlapped I/O: a synchronous pipe handle serialises its operations, so a read waiting
 * for Pythia would block the write that Pythia is waiting for. Each read and write waits on an event of its own.
 */
private class PipeStream(private val handle: COpaquePointer) : Stream {
    private val readEvent = CreateEventA(null, 1, 0, null)
    private val writeEvent = CreateEventA(null, 1, 0, null)

    /** Waits for the operation just started on [ov]; the bytes it moved, or -1 when the pipe has ended. */
    private fun finish(started: Int, ov: OVERLAPPED, moved: DWORDVar): Int {
        if (started == 0) {
            val error = GetLastError().toInt()
            if (error != ERROR_IO_PENDING && error != ERROR_MORE_DATA) return ended(error)
        }
        if (GetOverlappedResult(handle, ov.ptr, moved.ptr, 1) == 0) {
            val error = GetLastError().toInt()
            if (error != ERROR_MORE_DATA) return ended(error)
        }
        return moved.value.toInt()
    }

    private fun ended(error: Int): Int = when (error) {
        ERROR_BROKEN_PIPE, ERROR_PIPE_NOT_CONNECTED, ERROR_OPERATION_ABORTED, ERROR_HANDLE_EOF -> -1
        else -> throw StreamException(windowsError(error))
    }

    override fun read(buffer: ByteArray): Int {
        memScoped {
            val ov = alloc<OVERLAPPED>()
            val moved = alloc<DWORDVar>()
            while (true) {
                memset(ov.ptr, 0, sizeOf<OVERLAPPED>().convert())
                ov.hEvent = readEvent
                val started = buffer.usePinned { ReadFile(handle, it.addressOf(0), buffer.size.convert(), null, ov.ptr) }
                val n = finish(started, ov, moved)
                if (n != 0) return n
            }
        }
    }

    override fun write(data: ByteArray) {
        memScoped {
            val ov = alloc<OVERLAPPED>()
            val moved = alloc<DWORDVar>()
            var sent = 0
            while (sent < data.size) {
                memset(ov.ptr, 0, sizeOf<OVERLAPPED>().convert())
                ov.hEvent = writeEvent
                val started = data.usePinned {
                    WriteFile(handle, it.addressOf(sent), (data.size - sent).convert(), null, ov.ptr)
                }
                val n = finish(started, ov, moved)
                if (n < 0) throw StreamException("Pythia closed the connection.")
                sent += n
            }
        }
    }

    override fun close() {
        CancelIoEx(handle, null)
        CloseHandle(handle)
    }
}

actual fun openStream(address: String): Stream {
    var error = 0
    repeat(20) {
        val handle = CreateFileA(
            address,
            GENERIC_READ_BIT or GENERIC_WRITE_BIT,
            0u,
            null,
            OPEN_EXISTING_MODE,
            FILE_FLAG_OVERLAPPED_BIT,
            null,
        )
        // INVALID_HANDLE_VALUE is all bits set.
        if (handle != null && handle.toLong() != -1L) return PipeStream(handle)
        error = GetLastError().toInt()
        // Every instance of the pipe is serving someone else for a moment; Pythia makes the next one at once.
        if (error != ERROR_PIPE_BUSY) throw StreamException(windowsError(error))
        Sleep(100u)
    }
    throw StreamException(windowsError(error))
}
