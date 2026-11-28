"""Pythia's tray icon, always there while Pythia runs (docs/SPEC.md, decision 12). Closing the window leaves
Pythia running in the tray, serving paired handhelds; Quit Pythia from the tray's menu ends it.

The menu: Open Pythia, what the droidtop agent is doing (read when the menu opens), Start at sign-in, the
update (Check for updates, or Update to <version> once one is found, and the lane to follow; pythia.updater,
docs/SPEC.md decision 13), and Quit Pythia. Clicking the icon opens the window. Pythia looks for an update a
couple of minutes after it starts and every six hours, and says so in a tray message when one is out.
"""

import threading

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QIcon
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


def install(app: QApplication, icon_path: str, show_window=None) -> QSystemTrayIcon | None:
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
    open_window = show_window or (lambda: show_windows(app))
    open_action.triggered.connect(lambda: open_window())
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
    update_action, lane_menu = _update_actions(app, tray, menu)

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
    menu.addAction(update_action)
    menu.addMenu(lane_menu)
    menu.addSeparator()
    menu.addAction(quit_action)
    tray.setContextMenu(menu)
    tray.activated.connect(
        lambda reason: (
            open_window()
            if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick)
            else None
        )
    )
    tray._menu = menu  # noqa: SLF001 -- kept alive with the icon
    tray.show()
    return tray


UPDATE_CHECK_FIRST_MS = 2 * 60 * 1000
UPDATE_CHECK_EVERY_MS = 6 * 60 * 60 * 1000


def _update_actions(app: QApplication, tray: QSystemTrayIcon, menu: QMenu) -> tuple[QAction, QMenu]:
    """The tray's update row and its lane menu, and the background checks behind them."""
    from pythia import updater  # noqa: PLC0415

    main = MainThread()
    action = QAction("Check for updates", menu)
    lanes = QMenu("Update lane", menu)
    group = QActionGroup(lanes)
    found: dict = {}
    busy = threading.Event()

    def on_main(fn) -> None:
        main.run.emit(fn)

    def show(result: dict, announce: bool) -> None:
        found.clear()
        if result.get("state") == "newer":
            found.update(result["info"])
            action.setText(f"Update to {result['info'].get('versionName')}")
            if announce:
                tray.showMessage("Pythia", result["line"])
        else:
            action.setText("Check for updates")
            if announce:
                tray.showMessage("Pythia", result["line"])
        action.setEnabled(True)

    def check(announce: bool) -> None:
        if busy.is_set():
            return
        busy.set()
        action.setEnabled(False)
        action.setText("Checking for updates…")

        def work() -> None:
            try:
                result = updater.check()
            finally:
                busy.clear()
            on_main(lambda: show(result, announce or result.get("state") == "newer"))

        threading.Thread(target=work, daemon=True, name="pythia-update-check").start()

    def install() -> None:
        info = dict(found)
        busy.set()
        action.setEnabled(False)
        action.setText(f"Downloading {info.get('versionName')}…")

        def progress(fraction: float) -> None:
            on_main(lambda: action.setText(f"Downloading {info.get('versionName')}… {int(fraction * 100)}%"))

        def work() -> None:
            try:
                path = updater.download(info, progress)
            except (OSError, ValueError) as error:
                busy.clear()
                # Kept as text: Python unbinds the exception when the except block ends, before the Qt thread runs this.
                line = f"Couldn't update Pythia: {error}"
                on_main(lambda: show({"state": "error", "line": line}, True))
                return

            def hand_over() -> None:
                try:
                    updater.apply(path)
                except (OSError, ValueError) as error:
                    busy.clear()
                    show({"state": "error", "line": f"Couldn't update Pythia: {error}"}, True)
                    return
                app.quit()

            on_main(hand_over)

        threading.Thread(target=work, daemon=True, name="pythia-update").start()

    action.triggered.connect(lambda: install() if found else check(announce=True))

    def pick(lane: str) -> None:
        updater.set_lane(lane)
        check(announce=True)

    def fill_lanes() -> None:
        current = None

        def read() -> None:
            nonlocal current
            current = updater.lane() if updater.updatable() is None else None
            on_main(lambda: [a.setChecked(a.data() == current) for a in group.actions()])

        threading.Thread(target=read, daemon=True, name="pythia-update-lane").start()

    for lane in updater.LANES:
        item = QAction(updater.LANE_NAMES[lane], lanes)
        item.setCheckable(True)
        item.setData(lane)
        item.triggered.connect(lambda _checked=False, lane=lane: pick(lane))
        group.addAction(item)
        lanes.addAction(item)
    lanes.aboutToShow.connect(fill_lanes)
    if updater.updatable() is not None:
        action.setText("Updates come from where Pythia was installed")
        action.setEnabled(False)
        lanes.setEnabled(False)
    else:
        QTimer.singleShot(UPDATE_CHECK_FIRST_MS, lambda: check(announce=False))
        timer = QTimer(menu)
        timer.timeout.connect(lambda: check(announce=False))
        timer.start(UPDATE_CHECK_EVERY_MS)
        menu._update_timer = timer  # noqa: SLF001 -- kept alive with the menu
    menu._update_main = main  # noqa: SLF001
    return action, lanes
