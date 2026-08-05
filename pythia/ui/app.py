"""Pythia's Qt application entry point (2026-08-04) -- real replacement for
the deleted pythia/gui/application.py's GTK Application. Uses the real,
already-designed desktop UI ported from the old branch (pythia/ui/desktop/
app.py -- master-detail layout, Settings/Reconciliation dialogs, F95 login,
real design tokens), backed by pythia.ui.backend.Backend (the Qt bridge to
onboarding/scanning/reconciliation/plugins), not a placeholder.
"""

import sys

from PySide6.QtWidgets import QApplication

from pythia import settings
from pythia.database.games import rebuild_cache
from pythia.migrations import migrate
from pythia.startup import init_pythia, run_all_checks
from pythia.ui.backend import Backend
from pythia.ui.desktop.app import run as run_desktop
from pythia.util.log import logger


def run(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv

    logger.info("Starting Pythia %s (Qt UI)", settings.VERSION)
    init_pythia()
    migrate()
    rebuild_cache()
    run_all_checks(async_ops=True)

    qt_app = QApplication(argv)
    qt_app.setApplicationName("Pythia")

    backend = Backend()
    return run_desktop(qt_app, backend)


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
