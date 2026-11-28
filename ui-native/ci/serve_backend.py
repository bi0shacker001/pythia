"""Serves Pythia's real Backend on the backend channel, headless, for the native front end's integration job.

PYTHIA_DATA_DIR (a throwaway folder) holds this Pythia's key, database and socket, so the front end finds them by the
same lookup it uses on a person's computer. PYTHIA_SERVE_EMIT names a Backend signal without arguments to emit every
PYTHIA_SERVE_EMIT_MS milliseconds (default 1000). Prints "ready <address>" once the channel is up, then runs until
killed.
"""

import os
import sys
import threading


def main() -> int:
    if not os.environ.get("PYTHIA_DATA_DIR"):
        print("PYTHIA_DATA_DIR must name a throwaway folder.", file=sys.stderr)
        return 2
    os.environ.setdefault("PYTHIA_DATADIR_OVERRIDE", os.path.join(os.environ["PYTHIA_DATA_DIR"], "datadir"))

    from pythia.ipc import host, transport  # noqa: PLC0415
    from pythia.ui.backend import Backend  # noqa: PLC0415

    backend = Backend(desktop_events=False)
    server = host.start(backend)
    if server is None:
        print("The backend channel did not start.", file=sys.stderr)
        return 1
    print("ready", transport.address(), flush=True)
    stop = threading.Event()
    emit = os.environ.get("PYTHIA_SERVE_EMIT")
    try:
        if emit:
            signal = getattr(backend, emit)
            interval = int(os.environ.get("PYTHIA_SERVE_EMIT_MS", "1000")) / 1000
            while not stop.wait(interval):
                signal.emit()
        else:
            stop.wait()
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
