import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock


class CredentialPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.env = mock.patch.dict(os.environ, {"PYTHIA_DATA_DIR": self.tempdir.name})
        self.env.start()
        self.addCleanup(self.env.stop)

        from pythia import credentials

        self.credentials = credentials

    def test_multiple_values_survive_atomic_replacement(self):
        self.credentials.set_credential("plugin.one", "token", "alpha")
        self.credentials.set_credential("plugin.two", "cookie", "beta")

        self.assertEqual("alpha", self.credentials.get_credential("plugin.one", "token"))
        self.assertEqual("beta", self.credentials.get_credential("plugin.two", "cookie"))
        files = list((Path(self.tempdir.name) / "datadir" / "credentials").glob("*.json"))
        self.assertEqual(1, len(files))
        self.assertEqual("alpha", json.loads(files[0].read_text(encoding="utf-8"))["plugin.one"]["token"])
        self.assertEqual([], list(files[0].parent.glob("*.tmp")))

    def test_concurrent_updates_do_not_lose_values(self):
        threads = [
            threading.Thread(target=self.credentials.set_credential, args=("plugin", f"key-{i}", str(i)))
            for i in range(12)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        for i in range(12):
            self.assertEqual(str(i), self.credentials.get_credential("plugin", f"key-{i}"))

    def test_failed_replace_preserves_previous_file_and_cleans_temporary(self):
        self.credentials.set_credential("plugin", "token", "original")
        credentials_dir = Path(self.tempdir.name) / "datadir" / "credentials"

        with mock.patch.object(self.credentials.os, "replace", side_effect=OSError("simulated interruption")):
            with self.assertRaisesRegex(OSError, "simulated interruption"):
                self.credentials.set_credential("plugin", "token", "replacement")

        self.assertEqual("original", self.credentials.get_credential("plugin", "token"))
        self.assertEqual([], list(credentials_dir.glob("*.tmp")))

    def test_malformed_and_oversized_synced_files_are_ignored(self):
        credentials_dir = Path(self.tempdir.name) / "datadir" / "credentials"
        credentials_dir.mkdir(parents=True, exist_ok=True)
        (credentials_dir / "peer.json").write_text(
            json.dumps({"bad": "not-a-field-map", "mixed": {"valid": "yes", "invalid": ["no"]}}),
            encoding="utf-8",
        )

        self.assertIsNone(self.credentials.get_credential("bad", "token"))
        self.assertEqual("yes", self.credentials.get_credential("mixed", "valid"))
        self.assertIsNone(self.credentials.get_credential("mixed", "invalid"))

        (credentials_dir / "peer.json").write_bytes(b" " * (self.credentials.MAX_CREDENTIAL_FILE_BYTES + 1))
        self.assertIsNone(self.credentials.get_credential("mixed", "valid"))

        (credentials_dir / "peer.json").write_bytes(b"\xff\xfe invalid utf-8")
        self.assertIsNone(self.credentials.get_credential("mixed", "valid"))

    def test_empty_local_value_masks_synced_peer_credential(self):
        self.credentials.set_credential("plugin", "token", "")
        credentials_dir = Path(self.tempdir.name) / "datadir" / "credentials"
        (credentials_dir / "peer.json").write_text(
            json.dumps({"plugin": {"token": "peer-secret"}}), encoding="utf-8"
        )
        self.assertIsNone(self.credentials.get_credential("plugin", "token"))

    def test_oversized_write_is_rejected_without_replacing_existing_store(self):
        self.credentials.set_credential("plugin", "token", "original")
        with self.assertRaisesRegex(ValueError, "maximum file size"):
            self.credentials.set_credential(
                "plugin", "huge", "x" * self.credentials.MAX_CREDENTIAL_FILE_BYTES
            )
        self.assertEqual("original", self.credentials.get_credential("plugin", "token"))
        self.assertIsNone(self.credentials.get_credential("plugin", "huge"))


if __name__ == "__main__":
    unittest.main()
