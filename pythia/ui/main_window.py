"""Pythia's main window (2026-08-04) -- a real, working minimal replacement
for the deleted pythia/gui/pythiawindow.py. Deliberately small: lists real
games from the database and can launch/install them. The rich install
wizard, download queue UI, and category sidebar the old GTK window had are
not ported here yet -- this is a working foundation, not a finished window.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from pythia.database import games as games_db
from pythia.game import GAME_STARTED, GAME_STOPPED, Game
from pythia.ui.delegates_qt import QtInstallUIDelegate, QtLaunchUIDelegate
from pythia.util.log import logger


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Pythia")
        self.resize(900, 600)

        self._launch_delegate = QtLaunchUIDelegate(self)
        self._install_delegate = QtInstallUIDelegate(self)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        self.game_list = QListWidget()
        self.game_list.itemSelectionChanged.connect(self._on_selection_changed)
        layout.addWidget(self.game_list)

        buttons = QHBoxLayout()
        self.launch_button = QPushButton("Play")
        self.launch_button.clicked.connect(self._on_launch_clicked)
        self.launch_button.setEnabled(False)
        buttons.addWidget(self.launch_button)

        self.install_button = QPushButton("Install")
        self.install_button.clicked.connect(self._on_install_clicked)
        self.install_button.setEnabled(False)
        buttons.addWidget(self.install_button)
        layout.addLayout(buttons)

        self.setStatusBar(QStatusBar())

        GAME_STARTED.register(self._on_game_state_changed)
        GAME_STOPPED.register(self._on_game_state_changed)

        self.refresh_games()

    def refresh_games(self) -> None:
        self.game_list.clear()
        for db_game in games_db.get_games(sorts=[("name", "ASC")]):
            label = db_game["name"]
            if not db_game.get("installed"):
                label += "  (not installed)"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, db_game["id"])
            self.game_list.addItem(item)
        self.statusBar().showMessage(f"{self.game_list.count()} game(s)")

    def _selected_game_id(self) -> str | None:
        items = self.game_list.selectedItems()
        if not items:
            return None
        return items[0].data(Qt.ItemDataRole.UserRole)

    def _on_selection_changed(self) -> None:
        game_id = self._selected_game_id()
        if game_id is None:
            self.launch_button.setEnabled(False)
            self.install_button.setEnabled(False)
            return
        db_game = games_db.get_game_by_field(game_id, "id")
        installed = bool(db_game and db_game.get("installed"))
        self.launch_button.setEnabled(installed)
        self.install_button.setEnabled(not installed)

    def _on_launch_clicked(self) -> None:
        game_id = self._selected_game_id()
        if game_id is None:
            return
        game = Game(game_id)

        def on_error(error: BaseException) -> None:
            logger.exception("Unable to launch %s: %s", game.name, error)
            self.statusBar().showMessage(f"Failed to launch {game.name}: {error}")

        game.game_error.register(on_error)
        game.launch(self._launch_delegate)

    def _on_install_clicked(self) -> None:
        game_id = self._selected_game_id()
        if game_id is None:
            return
        game = Game(game_id)
        game.install(self._install_delegate)

    def _on_game_state_changed(self, game: Game) -> None:
        self.refresh_games()

    # ---- the app-singleton interface real core code calls (see
    # pythia.util.app_singleton and every services/*.py call site that does
    # get_default_application().show_installer_window(...)) ----
    def show_installer_window(self, installers, service=None, appid=None, installation_kind=None) -> None:
        if not installers:
            self.statusBar().showMessage("No installers available.")
            return
        try:
            from pythia.installer.interpreter import ScriptInterpreter  # noqa: PLC0415
        except ImportError as ex:
            self.statusBar().showMessage(
                f"Can't run installers yet: {ex}. The install wizard needs a Qt port of "
                "ScriptInterpreter (currently subclasses GObject.Object)."
            )
            return
        ScriptInterpreter(installers[0], self._install_delegate).launch_install(self._install_delegate)

    def show_pythia_installer_window(self, game_slug: str) -> None:
        from pythia.installer import get_installers  # noqa: PLC0415

        installers = get_installers(game_slug=game_slug)
        self.show_installer_window(installers)
