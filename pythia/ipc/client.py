"""A small blocking client of the backend channel: what the tests use, and `python -m pythia.ipc` for a look.

python -m pythia.ipc ping
python -m pythia.ipc call get_system_uuid
python -m pythia.ipc call get_play_state '["steam:440"]'
"""

import itertools
import json
import sys
from typing import Any

from pythia.ipc import framing, transport


class Client:
    def __init__(self, key: bytes, where: str | None = None, timeout: float = 10.0):
        self._stream = transport.connect(where, timeout=timeout)
        self._decoder = framing.FrameDecoder()
        self._inbox: list[dict[str, Any]] = []
        self._ids = itertools.count(1)
        self.events: list[dict[str, Any]] = []
        self.hello = self.call("hello", [key.hex()])

    def close(self) -> None:
        self._stream.close()

    def __enter__(self) -> "Client":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def send(self, message: dict[str, Any]) -> None:
        self._stream.send(framing.encode(message))

    def next_message(self) -> dict[str, Any] | None:
        """The next message from the server; None when it has closed the connection."""
        while not self._inbox:
            data = self._stream.recv()
            if not data:
                return None
            self._inbox.extend(self._decoder.feed(data))
        return self._inbox.pop(0)

    def call(self, method: str, args: Any = None) -> dict[str, Any]:
        """Calls and returns the answer; a pending one is waited for until it settles. Events that arrive on the
        way are kept in `events`."""
        call_id = next(self._ids)
        self.send({"id": call_id, "method": method, "args": [] if args is None else args})
        answer: dict[str, Any] | None = None
        while True:
            message = self.next_message()
            if message is None:
                return answer or {"status": "error", "message": "The connection closed.", "code": "closed"}
            if "event" in message:
                self.events.append(message)
            elif message.get("id") == call_id:
                answer = message["result"]
                if answer.get("status") != "pending" or message.get("settled"):
                    return answer


def main(argv: list[str] | None = None) -> int:
    from pythia import instance  # noqa: PLC0415

    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in ("ping", "call", "schema"):
        print(__doc__, file=sys.stderr)
        return 2
    try:
        client = Client(instance.channel_key())
    except OSError:
        print("Pythia is not running.", file=sys.stderr)
        return 1
    with client:
        if argv[0] == "call":
            answer = client.call(argv[1], json.loads(argv[2]) if len(argv) > 2 else [])
        else:
            answer = client.call(argv[0])
    print(json.dumps(answer, indent=2, ensure_ascii=False))
    return 0 if answer.get("status") == "ok" else 1
