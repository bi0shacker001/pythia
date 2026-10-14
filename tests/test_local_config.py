import tempfile
import unittest
from pathlib import Path

from pythia.util.local_config import load_local_config, save_local_config


class LocalConfigWriteTests(unittest.TestCase):
    def test_save_merge_reload_and_native_mode(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "pythia.cfg"
            result = save_local_config({"ui": "native", "debug": True}, path=path)
            self.assertTrue(result["restart_required"])
            self.assertEqual(load_local_config(path), {"debug": True, "ui": "native", "channel": "stable"})
            save_local_config({"channel": "nightly"}, path=path)
            self.assertEqual(load_local_config(path), {"debug": True, "ui": "native", "channel": "nightly"})

    def test_invalid_change_does_not_modify_file(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "pythia.cfg"
            save_local_config({"channel": "stable"}, path=path)
            before = path.read_bytes()
            for changes in ({"channel": "danger"}, {"debug": "yes"}, {"unknown": True}):
                with self.assertRaises(ValueError):
                    save_local_config(changes, path=path)
                self.assertEqual(path.read_bytes(), before)

    def test_preserves_unrelated_sections(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "pythia.cfg"
            path.write_text("[other]\nvalue = keep\n", encoding="utf-8")
            save_local_config({"ui": "desktop"}, path=path)
            self.assertIn("[other]", path.read_text(encoding="utf-8"))
            self.assertIn("value = keep", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
