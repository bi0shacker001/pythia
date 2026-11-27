"""Serialize the backend's pairing listener and retain its actual owner across UI connections."""

import threading
from collections.abc import Callable
from typing import Any


class PairingOffers:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._current: tuple[object | None, str, Callable[[], None]] | None = None

    def _withdraw(self) -> None:
        current = self._current
        if current is not None:
            current[2]()
            self._current = None

    def begin(
        self,
        owner: object | None,
        kind: str,
        start: Callable[[], dict[str, Any]],
        stop: Callable[[], None],
        alive: Callable[[], bool] = lambda: True,
    ) -> dict[str, Any]:
        with self._lock:
            if not alive():
                return {"status": "error", "message": "The pairing window has disconnected."}
            self._withdraw()
            try:
                answer = start()
            except BaseException:
                stop()
                raise
            self._current = (owner, kind, stop)
            # Disconnect can happen during a slow start, before its result reaches the caller.
            if not alive():
                self._withdraw()
                return {"status": "error", "message": "The pairing window has disconnected."}
            return answer

    def stop(self, owner: object | None, kind: str) -> bool:
        with self._lock:
            if self._current is None or self._current[0] is not owner or self._current[1] != kind:
                return False
            self._withdraw()
            return True

    def release(self, owner: object) -> None:
        with self._lock:
            if self._current is not None and self._current[0] is owner:
                self._withdraw()
