"""Helpers of the backend channel's tests: a private place for a channel, a server over a fake backend."""

import os
import tempfile
import uuid
from collections.abc import Callable
from typing import Any

KEY = bytes(range(32))

SCHEMA: dict[str, Any] = {
    "protocol": 1,
    "contract": "test",
    "methods": [
        {"name": "echo", "params": [{"name": "text", "type": "string"}], "returns": "answer"},
        {"name": "uuid", "params": [], "returns": "string"},
        {"name": "nothing", "params": [], "returns": "none"},
        {"name": "legacy_list", "params": [], "returns": "answer"},
        {"name": "broken", "params": [], "returns": "answer"},
        {"name": "garbage", "params": [], "returns": "answer"},
        {"name": "not_here", "params": [], "returns": "answer"},
        {"name": "slow", "params": [{"name": "n", "type": "integer"}], "returns": "answer", "async": True},
        {
            "name": "two",
            "params": [{"name": "a", "type": "string"}, {"name": "b", "type": "boolean", "optional": True}],
            "returns": "answer",
        },
    ],
    "events": [{"name": "gamesChanged", "params": []}, {"name": "logLine", "params": [{"type": "string"}]}],
}


class Place:
    """An address for one test's channel: a pipe name on Windows, a socket in a folder made for it elsewhere."""

    def __init__(self) -> None:
        self._folder: tempfile.TemporaryDirectory | None = None
        if os.name == "nt":
            self.where = r"\\.\pipe\pythia-test-" + uuid.uuid4().hex
        else:
            # Short: a Unix socket's path is limited to about 100 bytes.
            self._folder = tempfile.TemporaryDirectory(prefix="pyth", dir="/tmp")
            self.where = os.path.join(self._folder.name, "run", "b.sock")

    def cleanup(self) -> None:
        if self._folder is not None:
            self._folder.cleanup()


class FakeBackend:
    """What the server calls: a table of plain callables, and a record of the calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []

    def handlers(self) -> dict[str, Callable[..., Any]]:
        def record(name: str, fn: Callable[..., Any]) -> Callable[..., Any]:
            def run(*args: Any) -> Any:
                self.calls.append((name, args))
                return fn(*args)

            return run

        def broken() -> Any:
            raise RuntimeError("it broke")

        return {
            "echo": record("echo", lambda text: {"status": "ok", "text": text}),
            "uuid": record("uuid", lambda: "abc-123"),
            "nothing": record("nothing", lambda: None),
            "legacy_list": record("legacy_list", lambda: [{"id": "plugin.a"}]),
            "broken": record("broken", broken),
            "garbage": record("garbage", lambda: {"no": "status"}),
            "not_here": record("not_here", lambda: {"status": "unavailable", "message": "Not built yet."}),
            "slow": record("slow", lambda n: {"status": "pending", "slot": "slow", "request": f"slow-{n}"}),
            "two": record("two", lambda a, b=False: {"status": "ok", "a": a, "b": b}),
        }
