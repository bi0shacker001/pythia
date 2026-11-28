"""Run the Python backend and the shared Compose frontend without Qt."""

import os
import subprocess
import sys
import threading
from pathlib import Path

from pythia import instance
from pythia.droidtop import commands
from pythia.ipc import host
from pythia.ui.backend import Backend
from pythia.util.log import logger


def _frontend_environment() -> dict[str, str]:
    environment = os.environ.copy()
    if getattr(sys, "frozen", False) and sys.platform.startswith("linux"):
        # PyInstaller prepends its Python libraries for in-process extension
        # loading. The separate Compose executable resolves its own libraries.
        original = environment.pop("LD_LIBRARY_PATH_ORIG", None)
        if original is None:
            environment.pop("LD_LIBRARY_PATH", None)
        else:
            environment["LD_LIBRARY_PATH"] = original
    return environment


def run(executable: Path, *, error_console: bool = False) -> int:
    stop = threading.Event()
    ready = threading.Event()
    lock = threading.Lock()
    frontend: subprocess.Popen | None = None

    def show(_args, say):
        nonlocal frontend
        with lock:
            if not ready.is_set() or stop.is_set():
                return 1
            if frontend is not None and frontend.poll() is None:
                return 0
            try:
                frontend = subprocess.Popen([str(executable)], cwd=executable.parent, env=_frontend_environment())
            except OSError:
                logger.exception("Could not start the shared UI")
                say("Could not start the shared UI.")
                return 1
        return 0

    def quit_app(_args, _say):
        stop.set()
        return 0

    running = instance.Instance({"show": show, "quit": quit_app, **commands.HANDLERS})
    if not running.start():
        instance.request("show")
        return 0

    server = None
    try:
        backend = Backend(error_console=error_console, on_quit=stop.set)
        server = host.start(backend)
        if server is None:
            logger.error("Could not start the shared UI backend channel")
            return 1
        ready.set()
        if show([], logger.error):
            return 1
        while not stop.wait(0.2):
            with lock:
                result = frontend.poll() if frontend is not None else None
                if result is not None:
                    stop.set()
                    return result
        return 0
    finally:
        stop.set()
        with lock:
            child = frontend
        try:
            if child is not None and child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
        finally:
            if server is not None:
                server.stop()
            running.stop()
