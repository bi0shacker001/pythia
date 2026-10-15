"""
"UI-next" -- the real mockup HTML/CSS/JS (pythia/ui/desktop/web/index.html, the
signed-off Pass-5 mockup) rendered directly via QtWebEngine, bridged to the same
real `Backend` QObject (pythia/ui/backend.py) both other front ends already use.
Explicitly not a throwaway prototype (2026-08-03, user direction): this is the
fast-iteration testing path for UI changes going forward -- land something here
first, and once it's approved, port the confirmed piece into the QtWidgets
desktop UI (pythia/ui/desktop/app.py), which stays the stable/shipped target.

Same "Steam's client is basically a webpage" shape: a native window hosting a
Chromium-based view, with a thin JS<->Python bridge (QWebChannel) standing in
for what would otherwise be a REST/IPC layer. `Backend` needed zero changes to
work here -- it was already a plain QObject with @Property/@Slot members for
the QML big-picture front end, and QWebChannel exposes exactly that same
surface to JavaScript automatically.

Since 2026-10-14 every page of the mockup reads the backend through that bridge
(see pythia/ui/desktop/web/README.md for the page-to-slot table): a page with
no backend yet still calls its slot and shows the "unavailable" answer it gets.
The page's own JavaScript errors reach Pythia's log through
Backend.report_web_error, and the error console (`--error-console`, or
`error_console = True` in pythia.conf) shows that one log live.
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
    mockup bug. (The page also reports its errors to Backend.report_web_error
    itself, so they land in Pythia's log and the error console.)"""

    def javaScriptConsoleMessage(self, level, message, line_number, source_id):
        print(f"[web console] {source_id}:{line_number}: {message}", file=sys.stderr)


class WebviewWindow(QMainWindow):
    def __init__(self, backend):
        super().__init__()
        self.setWindowTitle("Pythia (UI-next)")
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
