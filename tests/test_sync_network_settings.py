import tempfile
import unittest
from pathlib import Path

from pythia.core.sync import network_settings


class NetworkSettingsTests(unittest.TestCase):
    def test_default_read_has_no_filesystem_side_effects(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "absent"
            self.assertEqual(network_settings.load(base_dir=base), network_settings.DEFAULTS)
            self.assertFalse(base.exists())

    def test_save_and_reload_round_trips(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            saved = network_settings.save(
                {"listen_port": 41000, "global_discovery_server": "https://discover.example"}, base_dir=base
            )
            self.assertEqual(41000, saved["listen_port"])
            self.assertEqual("0.0.0.0", saved["listen_host"])  # untouched default preserved
            reloaded = network_settings.load(base_dir=base)
            self.assertEqual(saved, reloaded)

    def test_rejects_invalid_port(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(network_settings.NetworkSettingsError):
                network_settings.save({"listen_port": 70000}, base_dir=Path(td))
            with self.assertRaises(network_settings.NetworkSettingsError):
                network_settings.save({"listen_port": 0}, base_dir=Path(td))
            with self.assertRaises(network_settings.NetworkSettingsError):
                network_settings.save({"listen_port": "8080"}, base_dir=Path(td))

    def test_rejects_unknown_key(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(network_settings.NetworkSettingsError):
                network_settings.save({"mystery": True}, base_dir=Path(td))

    def test_invalid_write_does_not_persist(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            try:
                network_settings.save({"listen_port": -1}, base_dir=base)
            except network_settings.NetworkSettingsError:
                pass
            self.assertEqual(network_settings.DEFAULTS, network_settings.load(base_dir=base))


if __name__ == "__main__":
    unittest.main()
