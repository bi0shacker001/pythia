"""
The web UI: pythia/ui/desktop/web (index.html, tokens.css, components.css,
components.js, app.js; README.md there documents the tokens, components and
behaviour) rendered by QtWebEngine and bridged over QWebChannel to the same
`Backend` QObject (pythia/ui/backend.py) the QML big-picture front end uses.
This is the fast-iteration design surface and the reference for the native
desktop UI (pythia/ui/desktop/app.py): a piece lands and is checked here
first, then the native UI implements the same component the same way.

Same shape as Steam's client: a native window hosting a Chromium view with a
thin JS<->Python bridge. The page shows only what `Backend` reports (its
properties, slots and signals); it carries no sample data of its own.
"""

import sys
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QMainWindow

_HTML_PATH = Path(__file__).parent.parent / "desktop" / "web" / "index.html"


class _LoggingPage(QWebEnginePage):
    """Surfaces the mockup's own JS console messages (errors especially) to
    stderr -- without this, a JS exception in the mockup's script just fails
    silently with no way to tell the real bridge/adapter code from a genuine
    mockup bug."""

    def javaScriptConsoleMessage(self, level, message, line_number, source_id):
        print(f"[web console] {source_id}:{line_number}: {message}", file=sys.stderr)


class WebviewWindow(QMainWindow):
    def __init__(self, backend):
        super().__init__()
        self.setWindowTitle("Pythia")
        self.resize(1280, 800)

        self._view = QWebEngineView(self)
        page = _LoggingPage(self._view)
        self._view.setPage(page)
        self.setCentralWidget(self._view)

        self._channel = QWebChannel(page)
        self._channel.registerObject("pythia", backend)
        page.setWebChannel(self._channel)

        self._view.load(QUrl.fromLocalFile(str(_HTML_PATH)))


def run(app, backend) -> int:
    window = WebviewWindow(backend)
    window.show()
    return app.exec()
