"""Serialize the backend's pairing listener and retain its actual owner across UI connections."""

import threading
from collections.abc import Callable
from typing import Any


class PairingChanged(RuntimeError):
    """A queued action no longer belongs to the offer the UI observed."""


class PairingOffers:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tickets = threading.Lock()
        self._generation = 0
        self._accepted = 0
        self._latest: dict[tuple[object | None, str], int] = {}
        self._cancelled: dict[tuple[object | None, str], int] = {}
        self._current: tuple[object | None, str, Callable[[], None], int] | None = None
        self._seen: dict[tuple[object | None, int], str] = {}

    def latest(self, owner: object | None, kind: str) -> int | None:
        with self._tickets:
            return self._latest.get((owner, kind))

    def reserve(self, owner: object | None, kind: str) -> int:
        # This lock never spans native work: Qt/IPC capture request order before scheduling worker threads.
        with self._tickets:
            self._generation += 1
            self._latest[owner, kind] = self._generation
            return self._generation

    def reserve_pick(self, owner: object | None) -> int | None:
        with self._tickets:
            key = (owner, "compare")
            previous = self._latest.get(key)
            if previous is not None and previous <= self._cancelled.get(key, 0):
                return None
            self._generation += 1
            self._latest[key] = self._generation
            return self._generation

    def begin_discovery(self, owner: object | None) -> None:
        # A fresh discovery sheet may start another pick after Stop. Retain the old cancellation cutoff,
        # so its already-queued workers stay cancelled; discovery itself must not replace another UI's offer.
        with self._tickets:
            key = (owner, "compare")
            previous = self._latest.get(key)
            if previous is not None and previous <= self._cancelled.get(key, 0):
                self._latest.pop(key, None)

    def request_stop(self, owner: object | None, kind: str) -> int | None:
        with self._tickets:
            generation = self._latest.get((owner, kind))
            if generation is not None:
                self._cancelled[owner, kind] = generation
            return generation

    def _allowed(self, owner: object | None, kind: str, generation: int) -> bool:
        with self._tickets:
            live = [
                value
                for key, value in self._latest.items()
                if value >= self._accepted and value > self._cancelled.get(key, 0)
            ]
            return generation >= self._accepted and generation == max(live, default=0)

    def _retired(self, generation: int) -> bool:
        with self._tickets:
            return generation < self._accepted

    def _abandon(self, owner: object | None, kind: str, generation: int) -> None:
        with self._tickets:
            key = (owner, kind)
            if self._latest.get(key) != generation:
                return
            self._latest.pop(key)
            current = self._current
            if (
                current is not None
                and current[0] is owner
                and current[1] == kind
                and current[3] <= generation
                and current[3] >= self._accepted
                and current[3] > self._cancelled.get(key, 0)
            ):
                self._latest[key] = current[3]

    def _withdraw(self) -> None:
        current = self._current
        if current is not None:
            current[2]()
            self._seen.pop((current[0], current[3]), None)
            self._current = None

    def _require_current(self, owner: object | None, kind: str, generation: int | None, alive: Callable[[], bool]):
        current = self._current
        if (
            generation is None
            or not alive()
            or current is None
            or current[0] is not owner
            or current[1] != kind
            or current[3] != generation
            or not self._allowed(owner, kind, generation)
        ):
            raise PairingChanged("The pairing request changed. Read the current code before trying again.")

    def observe(
        self, owner: object | None, kind: str, read: Callable[[], dict[str, Any]], alive: Callable[[], bool]
    ) -> dict[str, Any]:
        with self._lock:
            current = self._current
            if not alive():
                return {"state": "idle"}
            if current is None:
                latest = self.latest(owner, kind)
                if latest is not None and self._retired(latest):
                    return {"state": "failed", "error": "Another pairing request replaced this offer."}
                return {"state": "idle"}
            if current[0] is not owner or current[1] != kind:
                return {"state": "failed", "error": "Another pairing window owns this offer."}
            generation = current[3]
            if not self._allowed(owner, kind, generation):
                self._seen.pop((owner, generation), None)
                if self._retired(generation) or self.latest(owner, kind) is None:
                    return {"state": "failed", "error": "The pairing request was replaced or ended."}
                return {"state": "finishing"}
            answer = read()
            key = (owner, generation)
            code = answer.get("code")
            if answer.get("state") == "comparing" and isinstance(code, str) and code:
                self._seen[key] = code
            else:
                self._seen.pop(key, None)
            return answer

    def prepare(
        self,
        owner: object | None,
        kind: str,
        generation: int | None,
        prepare: Callable[[str | None], Any],
        alive: Callable[[], bool],
        needs_code: bool = False,
    ):
        with self._lock:
            self._require_current(owner, kind, generation, alive)
            code = self._seen.get((owner, generation)) if generation is not None else None
            if needs_code and not code:
                raise PairingChanged("Read and compare the current code before confirming it.")
            operation = prepare(code)
            try:
                self._require_current(owner, kind, generation, alive)
            except PairingChanged:
                operation.cancel()
                raise
            return operation

    def remember_comparison(
        self, owner: object | None, generation: int | None, code: Any, alive: Callable[[], bool]
    ) -> bool:
        with self._lock:
            try:
                self._require_current(owner, "compare", generation, alive)
            except PairingChanged:
                return False
            if generation is not None and isinstance(code, str) and code:
                self._seen[owner, generation] = code
            return True

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
                self.release(owner)
                return {"status": "error", "message": "The pairing window has disconnected."}
            if not self._allowed(owner, kind, generation):
                self._abandon(owner, kind, generation)
                return {"status": "error", "message": "Another pairing request replaced this offer."}
            try:
                self._withdraw()
            except BaseException:
                self._abandon(owner, kind, generation)
                raise
            with self._tickets:
                self._accepted = max(self._accepted, generation)
            try:
                answer = start()
            except BaseException:
                try:
                    stop()
                finally:
                    self._abandon(owner, kind, generation)
                raise
            self._current = (owner, kind, stop, generation)
            # Disconnect can happen during a slow start, before its result reaches the caller.
            if not alive():
                self.release(owner)
                return {"status": "error", "message": "The pairing request ended before startup finished."}
            if not self._allowed(owner, kind, generation):
                try:
                    self._withdraw()
                finally:
                    self._abandon(owner, kind, generation)
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
            try:
                if self._current is not None and self._current[0] is owner:
                    self._withdraw()
            finally:
                with self._tickets:
                    for key in [key for key in self._latest.keys() | self._cancelled.keys() if key[0] is owner]:
                        self._latest.pop(key, None)
                        self._cancelled.pop(key, None)
