"""
Decide which UI mode to start in, and own the lazy PySide6 import boundary: nothing
in `pythia.cli`'s subcommands (scan/onboard/list/renpy-patch) ever imports this module
or PySide6, so they keep working on a bare stdlib install. Only `python -m pythia`
with no subcommand reaches here.

Precedence: an explicit --ui hint > a saved choice of Big Picture (local.db, via
identity.get_setting -- not synced) > the web UI.

The web UI (pythia/ui/webview/app.py) is what Pythia starts in (UI review
2026-10-23, U1 and U3, Droidtop/tracker#538): it holds both the desktop and the
handheld shell and picks between them itself, by the device and the person's
"Start in" setting (pythia.start_shell). The QtWidgets "desktop" front end is
still a placeholder and Big Picture is the older handheld UI; each starts only
when asked for, by --ui or a saved choice of Big Picture.
"""

import sys

from pythia.identity import get_setting


def resolve_mode(app, hint: str | None) -> str:
    if hint in ("desktop", "bigpicture", "web"):
        return hint
    if get_setting("ui_mode") == "bigpicture":
        return "bigpicture"
    return "web"


def launch(hint: str | None = None, relaunch: bool = False, error_console: bool = False):
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print(
            "Pythia's UI needs PySide6, which isn't installed.\n"
            "Install it with:  pip install pyside6\n"
            "(CLI commands -- scan/onboard/list/renpy-patch -- don't need it.)",
            file=sys.stderr,
        )
        sys.exit(1)

    from pythia.ui import singleton

    if relaunch:
        singleton.terminate_existing_instance()

    # QApplication (a QGuiApplication subclass) works for both the QML big-picture
    # front end and the QtWidgets desktop one -- one Application instance is created
    # here, up front, and handed to whichever front end resolve_mode() picks.
    app = QApplication.instance() or QApplication(sys.argv)
    mode = resolve_mode(app, hint)

    singleton.write_pidfile()
    app.aboutToQuit.connect(singleton.clear_pidfile)

    from pythia.ui.backend import Backend

    backend = Backend(error_console=error_console)

    if mode == "bigpicture":
        from pythia.ui.bigpicture.app import run
    elif mode == "web":
        from pythia.ui.webview.app import run
    else:
        from pythia.ui.desktop.app import run

    sys.exit(run(app, backend))
