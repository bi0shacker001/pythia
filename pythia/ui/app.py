"""Pythia's Qt application entry point (2026-08-04) -- real replacement for
the deleted pythia/gui/application.py's GTK Application. Runs the real core
startup sequence (same as the headless CLI), then hands off to
pythia.ui.mode.launch() -- the real, already-designed mode selector that
picks between the three real front ends: the QtWidgets desktop UI
(pythia/ui/desktop/app.py, the stable/shipped target), the QML big-picture UI
(pythia/ui/bigpicture/app.py), and "UI-next" (pythia/ui/webview/app.py,
QtWebEngine rendering the real HTML/CSS/JS mockup at pythia/ui/desktop/web/
index.html) -- the fast-iteration testing surface for UI changes, explicit-
hint-only per mode.py's own docstring, never auto-selected.

Flags: `--ui <desktop|bigpicture|web>` picks the front end; `--error-console`
turns on the web UI's error console for this run (the config key
`error_console = True` in pythia.conf turns it on permanently). Off by default.
"""

import logging
import sys

from pythia import settings
from pythia.database.games import rebuild_cache
from pythia.migrations import migrate
from pythia.startup import init_pythia, run_all_checks
from pythia.util.log import logger


def run(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    ui_hint = None
    if "--ui" in argv:
        i = argv.index("--ui")
        if i + 1 < len(argv):
            ui_hint = argv[i + 1]
    error_console = "--error-console" in argv

    # Settings > Advanced > Log level (Backend.set_log_level writes the same key).
    saved_level = {"debug": logging.DEBUG, "info": logging.INFO}.get(settings.read_setting("log_level").lower())
    if saved_level is not None:
        logger.setLevel(saved_level)

    logger.info("Starting Pythia %s (Qt UI)", settings.VERSION)
    init_pythia()
    migrate()
    rebuild_cache()
    run_all_checks(async_ops=True)
    # While Pythia runs it is this computer's droidtop agent (pythia.droidtop):
    # it serves paired handhelds unless droidtop-agent already does.
    from pythia.droidtop import service

    service.start_in_background()

    from pythia.ui import mode

    mode.launch(hint=ui_hint, error_console=error_console)
    return 0


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
