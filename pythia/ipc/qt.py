"""The Qt side of the backend channel: serves the running Pythia's Backend to other processes.

Started by pythia.ui.mode right after the Backend is made, so it runs with the tray and the window, in the one
running Pythia (docs/SPEC.md, decision 12); another Pythia never gets here because `pythia.instance` turned it
away first. Calls run on the Qt thread, as the HTML UI's do over QWebChannel, and every signal of the Backend is
pushed to the connections.
"""

import threading
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, Signal

from pythia.ipc import host
from pythia.ipc.server import Server
from pythia.util.log import logger


class MainThreadCaller(QObject):
    """Runs a callable on the Qt thread (the thread this object was made on) and hands its result back."""

    _run = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self._thread = threading.current_thread()
        self._run.connect(lambda job: job())

    def call(self, fn: Callable[[], Any]) -> Any:
        if threading.current_thread() is self._thread:
            return fn()
        done = threading.Event()
        box: dict[str, Any] = {}

        def job() -> None:
            try:
                box["value"] = fn()
            except BaseException as ex:  # noqa: BLE001 -- handed back to the calling thread, which raises it
                box["error"] = ex
            finally:
                done.set()

        self._run.emit(job)
        done.wait()
        if "error" in box:
            raise box["error"]
        return box["value"]


def attach(app: Any, backend: Any, where: str | None = None) -> Server | None:
    """Serves `backend` on the backend channel until `app` quits. None when it cannot (another Pythia has the
    channel, or the folder is not private): the running Pythia carries on without it."""
    try:
        caller = MainThreadCaller()
        server = host.start(backend, where, caller.call)
        if server is None:
            logger.warning("The backend channel is already served; this Pythia does not serve it.")
            return None
    except Exception as ex:  # noqa: BLE001 -- the channel is an extra: Pythia runs without it
        logger.exception("The backend channel could not start: %s", ex)
        return None
    app.aboutToQuit.connect(server.stop)
    # Kept for the app's life, as pythia.ui.mode keeps its icon and its instance channel.
    app._pythia_ipc = (server, caller)  # noqa: SLF001
    return server
