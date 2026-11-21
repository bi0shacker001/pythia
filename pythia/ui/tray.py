"""Pythia's tray icon, always there while Pythia runs (docs/SPEC.md, decision 12). Closing the window leaves
Pythia running in the tray, serving paired handhelds; Quit Pythia from the tray's menu ends it.

The menu: Open Pythia, what the droidtop agent is doing (read when the menu opens), Start at sign-in, and
Quit Pythia. Clicking the icon opens the window.
"""

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from pythia.util.log import logger


class MainThread(QObject):
    """Runs a callable on the Qt thread: the instance channel answers on threads of its own."""

    run = Signal(object)

    def __init__(self):
        super().__init__()
        self.run.connect(lambda fn: fn())


def show_windows(app: QApplication) -> None:
    """Brings Pythia's window back, whichever front end drew it."""
    for widget in app.topLevelWidgets():
        if widget.isWindow() and widget.parent() is None and not isinstance(widget, QMenu) and widget.windowTitle():
            widget.showNormal()
            widget.raise_()
            widget.activateWindow()
    for window in app.topLevelWindows():
        if window.title() and not window.isVisible():
            window.show()
            window.raise_()
            window.requestActivate()


def hide_windows(app: QApplication) -> None:
    for widget in app.topLevelWidgets():
        if widget.isWindow() and widget.parent() is None and widget.windowTitle():
            widget.hide()
    for window in app.topLevelWindows():
        if window.title():
            window.hide()


def install(app: QApplication, icon_path: str) -> QSystemTrayIcon | None:
    """Puts the icon in the tray and keeps Pythia running when its window closes. None when the desktop has no
    tray, and then closing the window ends Pythia as before."""
    if not QSystemTrayIcon.isSystemTrayAvailable():
        logger.warning("This desktop has no tray: closing Pythia's window ends it")
        return None
    from pythia import autostart  # noqa: PLC0415
    from pythia.droidtop import service  # noqa: PLC0415

    app.setQuitOnLastWindowClosed(False)
    tray = QSystemTrayIcon(QIcon(icon_path), app)
    tray.setToolTip("Pythia")
    menu = QMenu()
    open_action = QAction("Open Pythia", menu)
    open_action.triggered.connect(lambda: show_windows(app))
    agent_line = QAction("", menu)
    agent_line.setEnabled(False)
    sign_in = QAction("Start at sign-in", menu)
    sign_in.setCheckable(True)
    quit_action = QAction("Quit Pythia", menu)
    quit_action.triggered.connect(app.quit)

    def toggle_sign_in(on: bool) -> None:
        try:
            autostart.set_enabled(on)
        except OSError as ex:
            logger.warning("Start at sign-in could not be changed: %s", ex)
            sign_in.setChecked(autostart.enabled())

    sign_in.toggled.connect(toggle_sign_in)

    def refresh() -> None:
        state = service.serving()
        if state.get("state") == "serving":
            agent_line.setText("Serving paired handhelds")
        else:
            agent_line.setText(state.get("reason") or "Not serving handhelds")
        sign_in.blockSignals(True)
        sign_in.setChecked(autostart.enabled())
        sign_in.blockSignals(False)

    menu.aboutToShow.connect(refresh)
    for action in (open_action, agent_line, sign_in):
        menu.addAction(action)
    menu.addSeparator()
    menu.addAction(quit_action)
    tray.setContextMenu(menu)
    tray.activated.connect(
        lambda reason: (
            show_windows(app)
            if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick)
            else None
        )
    )
    tray._menu = menu  # noqa: SLF001 -- kept alive with the icon
    tray.show()
    return tray
