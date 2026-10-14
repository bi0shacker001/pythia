import tempfile
import unittest
from pathlib import Path

from pythia import library_roots


class LibraryRootsTests(unittest.TestCase):
    def test_add_update_reload_and_remove(self):
        with tempfile.TemporaryDirectory() as td:
            base, games = Path(td) / "data", Path(td) / "games"
            games.mkdir()
            root = library_roots.add_root(str(games), name="Main", base_dir=base)
            self.assertEqual(library_roots.list_roots(base_dir=base)[0]["name"], "Main")
            updated = library_roots.update_root(root["id"], {"enabled": False, "recursive": False}, base_dir=base)
            self.assertFalse(updated["enabled"])
            self.assertFalse(updated["recursive"])
            library_roots.remove_root(root["id"], base_dir=base)
            self.assertEqual(library_roots.list_roots(base_dir=base), [])

    def test_duplicate_normalized_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            base, games = Path(td) / "data", Path(td) / "games"
            games.mkdir()
            library_roots.add_root(str(games), base_dir=base)
            with self.assertRaises(library_roots.LibraryRootError):
                library_roots.add_root(str(games / "."), base_dir=base)

    def test_missing_removable_root_can_be_persisted_for_nas(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "data"
            root = library_roots.add_root(str(Path(td) / "offline-nas"), require_exists=False, base_dir=base)
            self.assertTrue(root["enabled"])
            self.assertFalse(Path(root["path"]).exists())

    def test_bad_changes_do_not_replace_existing_store(self):
        with tempfile.TemporaryDirectory() as td:
            base, games = Path(td) / "data", Path(td) / "games"
            games.mkdir()
            root = library_roots.add_root(str(games), base_dir=base)
            with self.assertRaises(library_roots.LibraryRootError):
                library_roots.update_root(root["id"], {"enabled": "yes"}, base_dir=base)
            self.assertTrue(library_roots.list_roots(base_dir=base)[0]["enabled"])


if __name__ == "__main__":
    unittest.main()
