"""Serves Pythia's real Backend on the backend channel, headless, for the native front end's integration job.

PYTHIA_DATA_DIR (a throwaway folder) holds this Pythia's key, database and socket, so the front end finds them by the
same lookup it uses on a person's computer. Prints "ready <address>" once the channel is up, then runs until killed.
"""

import os
import sys


def main() -> int:
    if not os.environ.get("PYTHIA_DATA_DIR"):
        print("PYTHIA_DATA_DIR must name a throwaway folder.", file=sys.stderr)
        return 2
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("PYTHIA_DATADIR_OVERRIDE", os.path.join(os.environ["PYTHIA_DATA_DIR"], "datadir"))

    from PySide6.QtGui import QGuiApplication  # noqa: PLC0415

    app = QGuiApplication(sys.argv[:1])

    from pythia.ipc import qt as ipc  # noqa: PLC0415
    from pythia.ipc import transport  # noqa: PLC0415
    from pythia.ui.backend import Backend  # noqa: PLC0415

    backend = Backend()
    server = ipc.attach(app, backend)
    if server is None:
        print("The backend channel did not start.", file=sys.stderr)
        return 1
    print("ready", transport.address(), flush=True)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
