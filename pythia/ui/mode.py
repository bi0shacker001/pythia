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


def _become_the_running_pythia(app, tray_only: bool) -> bool:
    """This process becomes the one running Pythia (pythia.instance): it answers requests (show, quit, the
    droidtop commands), puts its icon in the tray and keeps running there when its window closes (docs/SPEC.md,
    decision 12). False when another Pythia already runs; that one is asked to show its window instead."""
    import os  # noqa: PLC0415

    from pythia import instance  # noqa: PLC0415
    from pythia.droidtop import commands  # noqa: PLC0415
    from pythia.ui import tray  # noqa: PLC0415
    from pythia.util import datapath  # noqa: PLC0415

    on_qt = tray.MainThread()

    def show(_args, _say):
        on_qt.run.emit(lambda: tray.show_windows(app))
        return 0

    def quit_app(_args, say):
        say("Pythia is quitting.")
        on_qt.run.emit(app.quit)
        return 0

    running = instance.Instance({"show": show, "quit": quit_app, **commands.HANDLERS})
    if not running.start():
        instance.request("show")
        return False
    app.aboutToQuit.connect(running.stop)
    icon = tray.install(app, os.path.join(datapath.get(), "media", "logo.png"))
    # Kept for the app's life: the icon, the channel and the hop onto the Qt thread.
    app.setProperty("pythiaRunning", True)
    app._pythia_keep = (on_qt, running, icon)  # noqa: SLF001
    if tray_only and icon is not None:
        from PySide6.QtCore import QTimer  # noqa: PLC0415

        QTimer.singleShot(0, lambda: tray.hide_windows(app))
    return True


def launch(hint: str | None = None, error_console: bool = False, tray_only: bool = False):
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

    # QApplication (a QGuiApplication subclass) works for both the QML big-picture
    # front end and the QtWidgets desktop one -- one Application instance is created
    # here, up front, and handed to whichever front end resolve_mode() picks.
    app = QApplication.instance() or QApplication(sys.argv)
    if not _become_the_running_pythia(app, tray_only):
        return
    mode = resolve_mode(app, hint)

    from pythia.ui.backend import Backend

    backend = Backend(error_console=error_console)

    if mode == "bigpicture":
        from pythia.ui.bigpicture.app import run
    elif mode == "web":
        from pythia.ui.webview.app import run
    else:
        from pythia.ui.desktop.app import run

    sys.exit(run(app, backend))
