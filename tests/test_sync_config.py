import tempfile
import unittest
from pathlib import Path

from pythia.core.sync import config


class SyncConfigTests(unittest.TestCase):
    def test_default_read_is_disabled_and_has_no_filesystem_side_effects(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "absent"
            self.assertFalse(config.is_enabled(base_dir=base))
            self.assertEqual(config.load(base_dir=base), config.DEFAULTS)
            self.assertFalse(base.exists())

    def test_explicit_enable_persists_without_creating_identity(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            saved = config.save({"enabled": True, "global_discovery": True}, base_dir=base)
            self.assertTrue(saved["enabled"])
            self.assertTrue(config.is_enabled(base_dir=base))
            self.assertTrue((base / "network" / "sync.json").is_file())
            self.assertFalse((base / "network" / "identity.json").exists())
            self.assertFalse((base / "network" / "cert.pem").exists())

    def test_rejects_unknown_or_non_boolean_values(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(config.SyncConfigError):
                config.save({"enabled": "yes"}, base_dir=Path(td))
            with self.assertRaises(config.SyncConfigError):
                config.save({"mystery": True}, base_dir=Path(td))


if __name__ == "__main__":
    unittest.main()
