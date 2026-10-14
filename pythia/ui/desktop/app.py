"""Default QtWidgets desktop entry point.

The maintained desktop implementation lives in ``pythia.ui.native``.  This
module preserves the long-standing ``pythia.ui.desktop.app`` import and
``run(app, backend)`` contract used by mode selection and core call sites.
"""

from pythia.ui.native.main_window import MainWindow


def run(app, backend) -> int:
    window = MainWindow(backend)

    from pythia.util import app_singleton

    app_singleton.set_default(window)
    window.show()
    try:
        return app.exec()
    finally:
        from pythia.util.log import shutdown_logging

        shutdown_logging()


__all__ = ["MainWindow", "run"]
