"""Store games found from the stores' own records (pythia.store_installs), and
Steam's and Epic's availability (Droidtop/tracker#538, the Windows UI test:
is_available was always False, and GOG, itch and Epic installs were never
found outside a scanned folder)."""

import os
import tempfile
from pathlib import Path
from unittest import TestCase, mock

from pythia import store_installs
from pythia.droidtop import native


def scan_of(*games):
    return {"games": list(games), "sources": [], "apps": [], "programs": [], "problems": []}


def store_game(key, title, folder, installed=True):
    return {
        "base": folder,
        "game": {"key": key, "title": title, "install": {"installed": installed, "launcher": key.split(":")[0]}},
    }


class FindTest(TestCase):
    def test_only_installed_games_with_a_folder_are_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = mock.Mock()
            fake.scan.scan_with.return_value = scan_of(
                store_game("epic:Fortnite", "Fortnite", tmp),
                store_game("gog:1207658691", "Gone", str(Path(tmp) / "missing")),
                store_game("steam:620", "Portal 2", tmp, installed=False),
            )
            with mock.patch.object(native, "_module", fake):
                found = store_installs.find()
        self.assertEqual([g["key"] for g in found], ["epic:Fortnite"])
        kwargs = fake.scan.scan_with.call_args.kwargs
        self.assertEqual(kwargs["game_folders"], [])
        self.assertIn("game folders", kwargs["disabled_sources"])


class RecordNewTest(TestCase):
    def test_new_folders_are_recorded_with_their_store_id_and_tracked_ones_left(self):
        with tempfile.TemporaryDirectory() as tmp:
            new, old = Path(tmp) / "Fortnite", Path(tmp) / "Portal 2"
            new.mkdir()
            old.mkdir()
            games = [
                {"key": "epic:Fortnite", "title": "Fortnite", "folder": str(new), "store": "epic"},
                {"key": "steam:620", "title": "Portal 2", "folder": str(old), "store": "steam"},
            ]
            tracked = [{"system_uuid": "me", "versions": [{"variants": [{"path": str(old)}]}]}]
            written = {"platform_ids": [], "ownership": []}
            saved = []
            with (
                mock.patch.object(store_installs, "find", return_value=games),
                mock.patch.object(store_installs, "get_system_uuid", return_value="me"),
                mock.patch.object(store_installs.datadir, "list_all_entries", return_value=tracked),
                mock.patch.object(
                    store_installs.onboarding, "onboard", return_value={"status": "ok", "path": "e.json"}
                ) as onboard,
                mock.patch.object(store_installs.datadir, "load_entry", return_value=written),
                mock.patch("pythia.database.games.save_entry_and_sync_cache", side_effect=saved.append),
            ):
                counts = store_installs.record_new()
        self.assertEqual((counts["recorded"], counts["already"], counts["failed"]), (1, 1, 0))
        onboard.assert_called_once_with(str(new))
        self.assertEqual(saved[0]["platform_ids"], [{"platform": "egs", "id": "Fortnite"}])
        self.assertEqual(saved[0]["ownership"], [{"platform": "egs"}])

    def test_a_folder_that_cannot_be_recorded_is_counted_and_said(self):
        with tempfile.TemporaryDirectory() as tmp:
            games = [{"key": "gog:1", "title": "G", "folder": tmp, "store": "gog"}]
            with (
                mock.patch.object(store_installs, "find", return_value=games),
                mock.patch.object(store_installs, "_tracked_folders", return_value=set()),
                mock.patch.object(
                    store_installs.onboarding, "onboard", return_value={"status": "error", "message": "no"}
                ),
            ):
                counts = store_installs.record_new()
        self.assertEqual(counts["failed"], 1)
        self.assertEqual(counts["failures"][0]["message"], "no")


class AvailabilityTest(TestCase):
    def test_steam_is_available_when_its_folder_is_found(self):
        from pythia.plugins.library import steam

        with mock.patch("pythia.util.steam.config.get_steam_dir", return_value="C:/Program Files (x86)/Steam/"):
            self.assertTrue(steam.plugin.is_available(None))
        with mock.patch("pythia.util.steam.config.get_steam_dir", return_value=None):
            self.assertFalse(steam.plugin.is_available(None))

    def test_epic_launcher_is_found_by_its_data_folder_on_windows_only(self):
        from pythia.plugins.library import egs

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "Epic" / "EpicGamesLauncher").mkdir(parents=True)
            with mock.patch.dict(os.environ, {"PROGRAMDATA": tmp}), mock.patch("sys.platform", "win32"):
                self.assertTrue(egs.epic_launcher_installed())
            with mock.patch.dict(os.environ, {"PROGRAMDATA": tmp}), mock.patch("sys.platform", "linux"):
                self.assertFalse(egs.epic_launcher_installed())
