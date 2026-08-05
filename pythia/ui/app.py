"""Pythia's Qt application entry point (2026-08-04) -- real replacement for
the deleted pythia/gui/application.py's GTK Application. Deliberately
minimal for now: registers itself as the app singleton (so services calling
pythia.util.app_singleton.get_default().show_installer_window(...) reach a
real window), initializes the same core startup sequence the CLI uses, and
shows the main window.
"""

import sys

from PySide6.QtWidgets import QApplication

from pythia import settings
from pythia.migrations import migrate
from pythia.startup import init_pythia, run_all_checks
from pythia.ui.main_window import MainWindow
from pythia.util import app_singleton
from pythia.util.log import logger


def run(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv

    logger.info("Starting Pythia %s (Qt UI)", settings.VERSION)
    init_pythia()
    migrate()
    run_all_checks(async_ops=True)

    qt_app = QApplication(argv)
    qt_app.setApplicationName("Pythia")

    window = MainWindow()
    app_singleton.set_default(window)
    window.show()

    return qt_app.exec()


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
