"""Entry point for the native PySide6 Qt Widgets UI -- same `run(app, backend)`
contract pythia.ui.desktop.app / pythia.ui.bigpicture.app / pythia.ui.webview.app
all use, so pythia.ui.mode.launch() can dispatch to this mode exactly like the
others. It is also the implementation behind the ordinary desktop entry point;
the explicit `--ui native` hint remains supported for compatibility."""

from __future__ import annotations


def run(app, backend) -> int:
    from pythia.ui.native.main_window import MainWindow

    window = MainWindow(backend)

    from pythia.util import app_singleton

    app_singleton.set_default(window)
    window.show()
    try:
        return app.exec()
    finally:
        from pythia.util.log import shutdown_logging

        shutdown_logging()
