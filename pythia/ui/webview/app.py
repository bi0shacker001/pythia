"""
QtWebEngine desktop shell, rendering pythia/ui/desktop/web/index.html and
bridged directly to the shared `Backend` QObject in pythia/ui/backend.py.

Same "Steam's client is basically a webpage" shape: a native window hosting a
Chromium-based view, with a thin JS<->Python bridge (QWebChannel) standing in
for what would otherwise be a REST/IPC layer. `Backend` needed zero changes to
work here -- it was already a plain QObject with @Property/@Slot members for
the QML big-picture front end, and QWebChannel exposes exactly that same
surface to JavaScript automatically.

The shell shares the production backend used by the native and QML interfaces;
individual unavailable controls remain explicitly disabled in the page.
"""

import json
import sys
from pathlib import Path

from PySide6.QtCore import QRect, QTimer, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QMainWindow

from pythia import identity

_HTML_PATH = Path(__file__).parent.parent / "desktop" / "web" / "index.html"
_GEOMETRY_SETTING_KEY = "webview_window_geometry"


class _LoggingPage(QWebEnginePage):
    """Surfaces the shell's JS console messages (errors especially) to
    stderr -- without this, a JS exception in the page just fails
    silently with no way to tell the real bridge/adapter code from a genuine
    front-end bug."""

    def javaScriptConsoleMessage(self, level, message, line_number, source_id):
        print(f"[web console] {source_id}:{line_number}: {message}", file=sys.stderr)


class WebviewWindow(QMainWindow):
    def __init__(self, backend):
        super().__init__()
        # "UI-next" is internal/developer terminology for this front end (see
        # this module's own docstring) -- the production-facing window title
        # must not leak it (2026-08-07, external review).
        self.setWindowTitle("Pythia")

        # Debounced, not on closeEvent: --relaunch's own termination
        # (singleton.py's terminate_existing_instance -> taskkill /F on Windows,
        # SIGTERM/SIGKILL on POSIX) force-kills the prior process without a normal
        # window close, so closeEvent never fires there -- the one flow this
        # exists for. Saving on move/resize instead means geometry is current
        # regardless of how the process ends. Created before the initial
        # resize()/_restore_geometry() below, since those themselves trigger
        # resizeEvent/moveEvent, which reference this timer.
        self._geometry_save_timer = QTimer(self)
        self._geometry_save_timer.setSingleShot(True)
        self._geometry_save_timer.setInterval(500)
        self._geometry_save_timer.timeout.connect(self._save_geometry)

        self.resize(1280, 800)
        self._restore_geometry()

        self._view = QWebEngineView(self)
        page = _LoggingPage(self._view)
        self._view.setPage(page)
        self.setCentralWidget(self._view)

        self._channel = QWebChannel(page)
        self._channel.registerObject("pythia", backend)
        page.setWebChannel(self._channel)

        self._view.load(QUrl.fromLocalFile(str(_HTML_PATH)))

    def _restore_geometry(self) -> None:
        """So `--relaunch` (close the running instance, start a fresh one) reopens
        in the same place and size instead of always resetting to the 1280x800
        default -- persisted the same way as enabled_plugins/saved_searches
        (identity.local.db, not cache.db, so it survives a relaunch)."""
        raw = identity.get_setting(_GEOMETRY_SETTING_KEY)
        if not raw:
            return
        try:
            geo = json.loads(raw)
            rect = QRect(geo["x"], geo["y"], geo["width"], geo["height"])
            maximized = bool(geo.get("maximized"))
        except (ValueError, KeyError, TypeError):
            return
        # Guard against a saved position landing off every connected monitor --
        # e.g. a monitor was unplugged/rearranged since the geometry was saved.
        # Without this, a bad saved value places the window somewhere the user
        # can never see or reach it again (hit for real, 2026-08-06).
        if not any(screen.availableGeometry().intersects(rect) for screen in QGuiApplication.screens()):
            return
        # Always set the normal-state geometry first, THEN maximize -- calling
        # showMaximized() on a window that hasn't been positioned yet lets Qt/
        # Windows guess which monitor to maximize onto, and it can guess wrong
        # (hit for real, 2026-08-06: maximized on a different monitor than the
        # one the window was actually last on).
        self.setGeometry(rect)
        if maximized:
            self.showMaximized()

    def _save_geometry(self) -> None:
        geo = self.normalGeometry()
        identity.set_setting(
            _GEOMETRY_SETTING_KEY,
            json.dumps(
                {
                    "x": geo.x(),
                    "y": geo.y(),
                    "width": geo.width(),
                    "height": geo.height(),
                    "maximized": self.isMaximized(),
                }
            ),
        )

    def _schedule_geometry_save(self) -> None:
        self._geometry_save_timer.start()

    def moveEvent(self, event) -> None:
        self._schedule_geometry_save()
        super().moveEvent(event)

    def resizeEvent(self, event) -> None:
        self._schedule_geometry_save()
        super().resizeEvent(event)

    def closeEvent(self, event) -> None:
        self._save_geometry()
        super().closeEvent(event)


def run(app, backend) -> int:
    window = WebviewWindow(backend)
    window.show()
    return app.exec()
