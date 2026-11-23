"""Length-prefixed JSON messages: a 4-byte big-endian length, then that many bytes of UTF-8 JSON (an object).

The same bytes on every transport; a reader may get them in any chunks, so `FrameDecoder` takes a stream.
"""

import json
import struct
from typing import Any

HEADER = struct.Struct(">I")
# Nothing Pythia sends is near this; a bigger length is a corrupt or hostile stream, not a big message.
MAX_FRAME = 16 * 1024 * 1024


class FrameError(ValueError):
    """The stream is not a sequence of frames; the connection cannot be trusted past this point."""


def encode(message: dict[str, Any]) -> bytes:
    body = json.dumps(message, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
    if len(body) > MAX_FRAME:
        raise FrameError(f"A message of {len(body)} bytes is over the {MAX_FRAME} byte limit.")
    return HEADER.pack(len(body)) + body


class FrameDecoder:
    """Collects bytes and hands back each complete message, in order."""

    def __init__(self, max_frame: int = MAX_FRAME):
        self._buffer = bytearray()
        self._max = max_frame

    def feed(self, data: bytes) -> list[dict[str, Any]]:
        self._buffer += data
        messages: list[dict[str, Any]] = []
        while len(self._buffer) >= HEADER.size:
            (length,) = HEADER.unpack_from(self._buffer)
            if length == 0 or length > self._max:
                raise FrameError(f"A frame of {length} bytes is not allowed.")
            end = HEADER.size + length
            if len(self._buffer) < end:
                break
            body = bytes(self._buffer[HEADER.size : end])
            del self._buffer[:end]
            try:
                message = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, ValueError) as ex:
                raise FrameError(f"A frame is not JSON: {ex}") from ex
            if not isinstance(message, dict):
                raise FrameError("A frame is not a JSON object.")
            messages.append(message)
        return messages

    @property
    def pending(self) -> int:
        """Bytes held that do not make a whole frame yet."""
        return len(self._buffer)
