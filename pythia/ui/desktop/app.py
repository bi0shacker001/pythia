"""The Python (QtWidgets) desktop UI -- this is going to be the default,
shipped UI, but it hasn't been written yet.

The previous occupant of this file was a leftover from many iterations ago
that kept getting dragged forward and re-tweaked instead of actually being
replaced -- gone. Real design/iteration work happens in the web UI
("UI-next", pythia/ui/webview/app.py + pythia/ui/desktop/web/index.html) --
once a piece of that is settled, it gets built here for real, modeled on
what was actually validated there, not invented independently.

This placeholder exists only so `pythia.ui.mode`'s "desktop" selection has
something real (if minimal) to launch, and so real core code calling
pythia.util.app_singleton.get_default().show_installer_window(...) reaches
an actual window instead of crashing on None.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QMainWindow


class MainWindow(QMainWindow):
    def __init__(self, backend):
        super().__init__()
        self.backend = backend
        self.setWindowTitle("Pythia")
        self.resize(900, 600)
        label = QLabel(
            "The desktop UI hasn't been built yet.\n\nRun with --ui web to use the real, in-progress design surface."
        )
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCentralWidget(label)

    # ---- the app-singleton interface real Lutris-derived core code calls
    # (see pythia.util.app_singleton and every services/*.py call site that
    # does get_default_application().show_installer_window(...)) ----
    def show_installer_window(self, installers, service=None, appid=None, installation_kind=None) -> None:
        pass

    def show_pythia_installer_window(self, game_slug: str) -> None:
        pass


def run(app, backend) -> int:
    window = MainWindow(backend)

    from pythia.util import app_singleton

    app_singleton.set_default(window)
    window.show()
    return app.exec()
