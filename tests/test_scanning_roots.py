import tempfile
import unittest
from pathlib import Path
from unittest import mock


class ConfiguredRootScanningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.old_data = __import__("os").environ.get("PYTHIA_DATA_DIR")
        __import__("os").environ["PYTHIA_DATA_DIR"] = self.temp.name

    def tearDown(self):
        if self.old_data is None:
            __import__("os").environ.pop("PYTHIA_DATA_DIR", None)
        else:
            __import__("os").environ["PYTHIA_DATA_DIR"] = self.old_data
        self.temp.cleanup()

    def test_enabled_recursive_state_and_offline_errors_are_honored(self):
        from pythia import library_roots, scanning

        root = Path(self.temp.name) / "games"
        root.mkdir()
        enabled = library_roots.add_root(str(root), recursive=False)
        library_roots.add_root(str(Path(self.temp.name) / "offline"), require_exists=False)
        disabled_path = Path(self.temp.name) / "disabled"
        disabled_path.mkdir()
        library_roots.add_root(str(disabled_path), enabled=False)
        progress = []
        with mock.patch("pythia.scanning.scan_library", return_value=[{"status": "ok"}]) as scan:
            result = scanning.scan_configured_roots(lambda root_row, status: progress.append((root_row, status)))
        scan.assert_called_once_with(Path(enabled["path"]), recursive=False)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["roots"], 1)
        self.assertEqual(result["disabled"], 1)
        self.assertEqual(result["game_count"], 1)
        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual(len(progress), 2)

    def test_one_scan_failure_does_not_prevent_other_roots(self):
        from pythia import library_roots, scanning

        a, b = Path(self.temp.name) / "a", Path(self.temp.name) / "b"
        a.mkdir(); b.mkdir()
        library_roots.add_root(str(a)); library_roots.add_root(str(b))
        with mock.patch("pythia.scanning.scan_library", side_effect=[RuntimeError("broken"), [{"status": "ok"}]]):
            result = scanning.scan_configured_roots()
        self.assertEqual(result["roots"], 1)
        self.assertEqual(result["game_count"], 1)
        self.assertEqual(result["errors"][0]["message"], "broken")


if __name__ == "__main__":
    unittest.main()
