from gettext import gettext as _
from typing import TYPE_CHECKING

from pythia.config import PythiaConfig
from pythia.gui.config.game_common import GameDialogCommon

if TYPE_CHECKING:
    from gi.repository import Gtk

    from pythia.game import Game


class AddGameDialog(GameDialogCommon):
    """Add game dialog class."""

    def __init__(self, parent: "Gtk.Widget", game: "Game | None" = None, runner: str | None = None):
        super().__init__(_("Add a new game"), config_level="game", parent=parent)
        self.game = game
        self.saved = False

        self.pythia_config = PythiaConfig(
            runner_slug=game.runner_name if game else runner,
            level="game",
        )
        self.build_notebook()
        self.build_tabs()
        if self.info_box:
            self.info_box.grab_focus()
        self.show_all()
