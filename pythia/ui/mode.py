"""
Decide which UI mode to start in, and own the lazy PySide6 import boundary: nothing
in `pythia.cli`'s subcommands (scan/onboard/list/renpy-patch) ever imports this module
or PySide6, so they keep working on a bare stdlib install. Only `python -m pythia`
with no subcommand reaches here.

Precedence: an explicit --ui hint > a saved per-system preference (local.db, via
identity.get_setting -- not synced, matches how "settings" is described in the
Milestone 1 data layout) > a screen-size heuristic > desktop by default.
"""

import sys

from pythia.identity import get_setting

# Below this on the screen's shorter dimension (px), default to big-picture -- sized
# to catch small handhelds (e.g. a Retroid Pocket 5) while leaving normal desktop and
# even most tablet-class displays in desktop mode.
SMALL_SCREEN_THRESHOLD_PX = 900


def _detect_screen_mode(app) -> str:
    screen = app.primaryScreen()
    if screen is None:
        return "desktop"
    size = screen.size()
    if min(size.width(), size.height()) <= SMALL_SCREEN_THRESHOLD_PX:
        return "bigpicture"
    return "desktop"


def resolve_mode(app, hint: str | None) -> str:
    if hint in ("desktop", "bigpicture"):
        return hint
    saved = get_setting("ui_mode")
    if saved in ("desktop", "bigpicture"):
        return saved
    return _detect_screen_mode(app)


def launch(hint: str | None = None):
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
    mode = resolve_mode(app, hint)

    from pythia.ui.backend import Backend

    backend = Backend()

    if mode == "bigpicture":
        from pythia.ui.bigpicture.app import run
    else:
        from pythia.ui.desktop.app import run

    sys.exit(run(app, backend))
