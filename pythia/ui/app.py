"""Pythia's Qt application entry point (2026-08-04) -- real replacement for
the deleted pythia/gui/application.py's GTK Application. Runs the real core
startup sequence (same as the headless CLI), then hands off to
pythia.ui.mode.launch() -- the real, already-designed mode selector that
picks between the three real front ends: the maintained QtWidgets desktop UI
(pythia/ui/desktop/app.py -- the default), the QML
big-picture UI (pythia/ui/bigpicture/app.py), and "UI-next"
(pythia/ui/webview/app.py, QtWebEngine rendering the maintained HTML/CSS/JS shell
at pythia/ui/desktop/web/index.html) -- the fast-iteration testing surface for
UI changes, explicit-hint-only per mode.py's own docstring, never
auto-selected.

`--relaunch` closes whatever running instance singleton.py's PID file points
at, then starts this one fresh in its place -- see pythia.ui.singleton. Handy
for picking up a code change without manually killing the old process first,
which is exactly the loop this file goes through most during UI development.
"""

import sys

from pythia import settings
from pythia.database.games import rebuild_cache
from pythia.migrations import migrate
from pythia.startup import init_pythia, run_all_checks
from pythia.util.local_config import load_local_config
from pythia.util.log import logger, set_debug_logging


def _install_excepthook() -> None:
    """The one guaranteed diagnostic path for a startup crash in a
    --windowed (no console) build (2026-08-07, "make debug logging
    possible" -- without this, an uncaught exception before the Qt event
    loop even starts had nowhere to go at all in a packaged build)."""

    def _hook(exc_type, exc_value, exc_tb):
        logger.critical("Uncaught exception", exc_info=(exc_type, exc_value, exc_tb))
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = _hook


def run(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    _install_excepthook()

    # pythia.cfg (see pythia.util.local_config) is the baseline -- explicit
    # CLI flags always win over it, same precedence a config file vs. a
    # command-line flag always has (2026-08-07: "We're gonna use a config
    # file on windows... It controls debug, ui selection, and etc.").
    local_config = load_local_config()
    if "--debug" in argv or "-v" in argv or local_config["debug"]:
        set_debug_logging(True)
    ui_hint = local_config["ui"]
    if "--ui" in argv:
        i = argv.index("--ui")
        if i + 1 < len(argv):
            ui_hint = argv[i + 1]
    relaunch = "--relaunch" in argv

    logger.info("Starting Pythia %s%s", settings.VERSION, " (relaunch)" if relaunch else "")
    init_pythia()
    migrate()
    rebuild_cache()
    run_all_checks(async_ops=True)

    from pythia.ui import mode

    mode.launch(hint=ui_hint, relaunch=relaunch)
    return 0


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
