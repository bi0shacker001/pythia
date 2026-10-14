"""Real tests for pythia.ui.sync_bridge.SyncBridge -- the Qt-independent
business layer Backend's Slots call into. Isolation is mandatory: SyncBridge
uses SyncService with no base_dir override (the real production path), which
resolves to pythia.identity.app_data_dir() -- real %APPDATA%/Pythia unless
PYTHIA_DATA_DIR is overridden. Every test sets/restores it, the same
lesson-learned pattern as tests/test_download_automator.py.
"""

import os
import tempfile
import unittest

from pythia.core.sync import config as sync_config


class SyncBridgeTests(unittest.TestCase):
    def setUp(self):
        self._data_dir = tempfile.TemporaryDirectory()
        self._prior_data_dir = os.environ.get("PYTHIA_DATA_DIR")
        os.environ["PYTHIA_DATA_DIR"] = self._data_dir.name

        from pythia.ui.sync_bridge import SyncBridge

        self.bridge = SyncBridge()

    def tearDown(self):
        self.bridge.stop()
        if self._prior_data_dir is None:
            os.environ.pop("PYTHIA_DATA_DIR", None)
        else:
            os.environ["PYTHIA_DATA_DIR"] = self._prior_data_dir
        self._data_dir.cleanup()

    def test_constructing_bridge_has_zero_side_effects(self):
        from pathlib import Path

        base = Path(self._data_dir.name)
        self.assertFalse((base / "network").exists())
        self.assertFalse(self.bridge.running)

    def test_ensure_started_while_disabled_is_a_real_no_op(self):
        result = self.bridge.ensure_started()
        self.assertEqual({"status": "disabled"}, result)
        self.assertFalse(self.bridge.running)
        from pathlib import Path

        base = Path(self._data_dir.name)
        self.assertFalse((base / "network" / "identity.json").exists())

    def test_set_enabled_true_starts_and_creates_identity(self):
        result = self.bridge.set_enabled(True)
        self.assertIn(result["status"], ("ok", "partial"))
        if result["status"] == "partial":
            self.assertTrue(result["lan_discovery_error"])
        self.assertTrue(self.bridge.running)
        status = self.bridge.get_status()
        self.assertTrue(status["running"])
        self.assertIsNotNone(status["device_id"])
        self.assertIsNotNone(status["system_uuid"])
        self.assertIn("lan_discovery", status)

    def test_set_enabled_false_stops(self):
        self.bridge.set_enabled(True)
        self.assertTrue(self.bridge.running)
        result = self.bridge.set_enabled(False)
        self.assertEqual("ok", result["status"])
        self.assertFalse(self.bridge.running)
        self.assertFalse(sync_config.is_enabled())

    def test_system_name_requires_sync_to_be_running(self):
        with self.assertRaises(RuntimeError):
            self.bridge.set_system_name("My PC")
        self.bridge.set_enabled(True)
        record = self.bridge.set_system_name("My PC")
        self.assertEqual("My PC", record["system_name"])

    def test_pairing_invitation_requires_sync_to_be_running(self):
        with self.assertRaises(RuntimeError):
            self.bridge.create_pairing_invitation()
        self.bridge.set_enabled(True)
        text = self.bridge.create_pairing_invitation(friendly_name="My PC")
        self.assertTrue(text.startswith("pythia-pair:"))

    def _real_valid_device_id(self) -> str:
        """A real, check-digit-valid device ID, derived the same way any
        real device gets one -- generated into its own isolated temp
        base_dir so it never touches this test's own identity."""
        from pythia.core.sync import identity

        with tempfile.TemporaryDirectory() as other_base:
            record = identity.get_or_create_identity("Other PC", base_dir=__import__("pathlib").Path(other_base))
            return record["device_id"]

    def test_manual_pairing_rejects_self(self):
        self.bridge.set_enabled(True)
        status = self.bridge.get_status()
        with self.assertRaises(ValueError):
            self.bridge.pair_manually(
                system_uuid=status["system_uuid"], device_id=self._real_valid_device_id(), friendly_name="Myself"
            )

    def test_list_and_remove_trusted_round_trips(self):
        self.bridge.set_enabled(True)
        self.assertEqual([], self.bridge.list_trusted())
        self.bridge.pair_manually(
            system_uuid="other-system", device_id=self._real_valid_device_id(), friendly_name="Other PC"
        )
        trusted = self.bridge.list_trusted()
        self.assertEqual(1, len(trusted))
        self.assertEqual("other-system", trusted[0]["system_uuid"])
        self.assertTrue(self.bridge.remove_trusted("other-system"))
        self.assertEqual([], self.bridge.list_trusted())

    def test_network_settings_round_trip(self):
        saved = self.bridge.set_network_settings({"listen_port": 41111})
        self.assertEqual(41111, saved["listen_port"])
        self.assertEqual(41111, self.bridge.get_network_settings()["listen_port"])

    def test_cancel_transfer_for_unknown_peer_returns_false(self):
        self.bridge.set_enabled(True)
        self.assertFalse(self.bridge.cancel_transfer("no-such-peer"))

    def test_retry_state_delegates_to_service(self):
        self.bridge.set_enabled(True)
        self.assertEqual({}, self.bridge.retry_state())

    def test_staging_export_then_import_round_trips_real_items(self):
        from pythia.core.sync import metadata

        record = metadata.create_record(
            {"system_uuid": "peer-1", "game_id": "g1", "title": "Staged Game"}, revision=1
        )
        with tempfile.TemporaryDirectory() as staging_root:
            export_result = self.bridge.export_staging_snapshot(staging_root, [record])
            self.assertEqual("ok", export_result["status"])
            self.assertEqual(1, export_result["items"])

            import_result = self.bridge.import_staging_snapshot(staging_root)
            self.assertEqual("ok", import_result["status"])
            self.assertEqual(1, len(import_result["items"]))
            self.assertEqual("g1", import_result["items"][0]["game_id"])

    def test_toggling_relay_discovery_does_not_restart_the_listener(self):
        """2026-08-08, Codex sync review item 5, "Make live capability
        changes apply safely without restarting unrelated sync
        components" -- the real listener object must be untouched."""
        self.bridge.set_enabled(True)
        listener_before = self.bridge._service._listener
        self.bridge.set_config({"relay_discovery": True})
        self.assertIs(listener_before, self.bridge._service._listener)

    def test_toggling_global_discovery_does_not_restart_the_listener(self):
        self.bridge.set_enabled(True)
        listener_before = self.bridge._service._listener
        self.bridge.set_config({"global_discovery": True})
        self.assertIs(listener_before, self.bridge._service._listener)

    def test_toggling_lan_discovery_does_require_a_real_restart(self):
        """A setting that genuinely needs the socket rebound must still get
        a real, honest full restart -- never a silent no-op that would
        leave the old (now-stale) config in effect."""
        self.bridge.set_enabled(True)
        listener_before = self.bridge._service._listener
        self.bridge.set_config({"lan_discovery": False})
        self.assertIsNot(listener_before, self.bridge._service._listener)

    def test_toggling_nat_traversal_does_not_restart_the_listener(self):
        self.bridge.set_enabled(True)
        listener_before = self.bridge._service._listener
        self.bridge.set_config({"nat_traversal": True})
        self.assertIs(listener_before, self.bridge._service._listener)

    def test_nat_status_reports_real_inactive_state_by_default(self):
        self.bridge.set_enabled(True)
        status = self.bridge.nat_status()
        self.assertFalse(status["active"])
        self.assertIsNone(status["protocol"])


if __name__ == "__main__":
    unittest.main()
