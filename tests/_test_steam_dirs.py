"""Steam's folders on Windows, and the Steam plugin's SteamID lookup
(Droidtop/tracker#538: --list-steam-folders found nothing on Windows)."""

import os
import tempfile
from unittest import TestCase, mock

from pythia.plugins.library import steam as steam_plugin
from pythia.util.steam import config


class WindowsSteamDirsTest(TestCase):
    def test_default_program_files_folders_without_the_registry(self):
        env = {"ProgramFiles(x86)": r"C:\Program Files (x86)", "ProgramFiles": r"C:\Program Files"}
        with mock.patch.object(config.sys, "platform", "linux"), mock.patch.dict(os.environ, env):
            dirs = config._windows_steam_dirs()
        self.assertEqual(
            dirs, (os.path.join(env["ProgramFiles(x86)"], "Steam"), os.path.join(env["ProgramFiles"], "Steam"))
        )

    def test_steamapps_is_found_in_a_listed_folder(self):
        with tempfile.TemporaryDirectory() as steam:
            os.makedirs(os.path.join(steam, "steamapps"))
            with (
                mock.patch.object(config, "STEAM_DATA_DIRS", (steam,)),
                mock.patch.object(config, "get_steam_config", return_value=None),
                mock.patch.object(config, "get_library_config", return_value=None),
            ):
                self.assertIn(os.path.join(steam, "steamapps"), list(config.get_steamapps_dirs()))


class SteamIdTest(TestCase):
    def test_the_active_account_is_looked_up_without_an_override(self):
        with (
            mock.patch.object(steam_plugin.plugin, "get_credential", return_value=None),
            mock.patch.object(config, "get_steam_users", return_value=[{"steamid64": "76561197960287930"}]),
            mock.patch.object(config.settings, "read_setting", return_value=""),
        ):
            self.assertEqual(steam_plugin.get_steamid64(), "76561197960287930")
