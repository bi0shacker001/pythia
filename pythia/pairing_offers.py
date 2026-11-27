"""Serialize the backend's pairing listener and retain its actual owner across UI connections."""

import threading
from collections.abc import Callable
from typing import Any


class PairingOffers:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tickets = threading.Lock()
        self._generation = 0
        self._latest: dict[tuple[object | None, str], int] = {}
        self._cancelled: dict[tuple[object | None, str], int] = {}
        self._current: tuple[object | None, str, Callable[[], None], int] | None = None

    def reserve(self, owner: object | None, kind: str) -> int:
        # This lock never spans native work: Qt/IPC capture request order before scheduling worker threads.
        with self._tickets:
            self._generation += 1
            self._latest[owner, kind] = self._generation
            return self._generation

    def request_stop(self, owner: object | None, kind: str) -> int | None:
        with self._tickets:
            generation = self._latest.get((owner, kind))
            if generation is not None:
                self._cancelled[owner, kind] = generation
            return generation

    def _allowed(self, owner: object | None, kind: str, generation: int) -> bool:
        with self._tickets:
            return generation == self._generation and generation > self._cancelled.get((owner, kind), 0)

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
        generation: int | None = None,
    ) -> dict[str, Any]:
        if generation is None:
            generation = self.reserve(owner, kind)
        with self._lock:
            if not alive():
                return {"status": "error", "message": "The pairing window has disconnected."}
            if not self._allowed(owner, kind, generation):
                return {"status": "error", "message": "Another pairing request replaced this offer."}
            self._withdraw()
            try:
                answer = start()
            except BaseException:
                stop()
                raise
            self._current = (owner, kind, stop, generation)
            # Disconnect can happen during a slow start, before its result reaches the caller.
            if not alive() or not self._allowed(owner, kind, generation):
                self._withdraw()
                return {"status": "error", "message": "The pairing request ended before startup finished."}
            return answer

    def stop(self, owner: object | None, kind: str, generation: int | None) -> bool:
        with self._lock:
            if (
                generation is None
                or self._current is None
                or self._current[0] is not owner
                or self._current[1] != kind
                or self._current[3] > generation
            ):
                return False
            self._withdraw()
            return True

    def release(self, owner: object) -> None:
        with self._lock:
            if self._current is not None and self._current[0] is owner:
                self._withdraw()
            with self._tickets:
                for key in [key for key in self._latest if key[0] is owner]:
                    self._latest.pop(key, None)
                    self._cancelled.pop(key, None)
