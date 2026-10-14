"""Background asyncio event loop for driving pythia.core.sync's async APIs
from synchronous callers (2026-08-08, "optional core sync service and native
device/transfer workflow"). pythia/ui/backend.py's Qt Slots are synchronous
methods running on the GUI thread or on Backend._run_worker()'s own daemon
threads, but every pythia.core.sync module (SyncService included) is async.

One SyncRuntime owns exactly one persistent daemon thread running its own
asyncio event loop. The thread is started only on first real use of
ensure_started()/submit() -- never merely from importing this module or
constructing a SyncRuntime -- preserving the same "dormant until actually
used" contract SyncService itself already provides.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from typing import Coroutine


class SyncRuntime:
    def __init__(self):
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._loop is not None

    def ensure_started(self) -> None:
        """Real side effect: spawns the background loop thread, once. Safe to
        call from any thread, repeatedly -- a no-op if already running."""
        with self._lock:
            if self._loop is not None:
                return
            ready = threading.Event()
            started_loop: dict = {}

            def _run() -> None:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                started_loop["loop"] = loop
                ready.set()
                loop.run_forever()
                loop.close()

            self._thread = threading.Thread(target=_run, daemon=True, name="pythia-sync-runtime")
            self._thread.start()
            ready.wait()
            self._loop = started_loop["loop"]

    def submit(self, coro: Coroutine) -> concurrent.futures.Future:
        """Schedule `coro` on the runtime's loop from any thread. Starts the
        loop on first use. Returns a concurrent.futures.Future; call
        `.result(timeout=...)` to block for the outcome -- do this only from
        a background thread (e.g. inside Backend._run_worker()'s own thread),
        never from the Qt GUI thread."""
        self.ensure_started()
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def call(self, coro: Coroutine, *, timeout: float | None = None):
        """Convenience: submit and block for the real result (or re-raise the
        real exception) -- never swallows a failure into a fake success."""
        return self.submit(coro).result(timeout=timeout)

    def stop(self) -> None:
        """Stops the loop and joins its thread. A real no-op if never
        started. After this returns, a later submit()/call() will transparently
        start a fresh loop -- the runtime is reusable, not single-shot."""
        with self._lock:
            loop = self._loop
            thread = self._thread
            if loop is None:
                return
            loop.call_soon_threadsafe(loop.stop)
            if thread is not None:
                thread.join(timeout=5)
            self._loop = None
            self._thread = None
