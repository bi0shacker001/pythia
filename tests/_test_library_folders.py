"""The library folders: one list with the droidtop agent's game and ROM folders (pythia.droidtop.store)."""

from unittest import TestCase, mock

from pythia.droidtop import store


class FakeStore:
    def __init__(self, settings):
        self._settings = dict(settings)
        self.changes = []

    def settings(self):
        return dict(self._settings)

    def set_settings(self, **changes):
        self.changes.append(changes)
        self._settings.update(changes)


class LibraryFoldersTest(TestCase):
    def test_the_list_is_the_agents_game_and_rom_folders(self):
        fake = FakeStore({"game_folders": ["G:\\games"], "rom_folders": ["G:\\roms"], "scan_minutes": 15})
        with mock.patch.object(store, "submodule", return_value=fake):
            self.assertEqual({"games": ["G:\\games"], "roms": ["G:\\roms"]}, store.library_folders())
            self.assertEqual(15, store.scan_minutes())

    def test_setting_one_kind_leaves_the_other(self):
        fake = FakeStore({"game_folders": [], "rom_folders": ["G:\\roms"]})
        with mock.patch.object(store, "submodule", return_value=fake):
            store.set_library_folders(games=["G:\\games"])
            self.assertEqual([{"game_folders": ["G:\\games"]}], fake.changes)
            self.assertEqual({"games": ["G:\\games"], "roms": ["G:\\roms"]}, store.library_folders())

    def test_no_interval_set_scans_every_half_hour(self):
        with mock.patch.object(store, "submodule", return_value=FakeStore({})):
            self.assertEqual(30, store.scan_minutes())
