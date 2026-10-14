import os
import tempfile
import unittest


class SyncIdentityTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        os.environ["PYTHIA_DATA_DIR"] = self._tmpdir.name

    def tearDown(self):
        self._tmpdir.cleanup()
        os.environ.pop("PYTHIA_DATA_DIR", None)

    def test_device_id_format_and_check_digits(self):
        from pythia.core.sync import identity

        record = identity.get_or_create_identity("Test System")
        device_id = record["device_id"]

        groups = device_id.split("-")
        self.assertEqual(8, len(groups))
        for g in groups:
            self.assertEqual(7, len(g))
        self.assertTrue(identity.validate_device_id(device_id))

        # A single mutated character must fail the check-digit validation.
        mutated = ("A" if device_id[0] != "A" else "B") + device_id[1:]
        self.assertFalse(identity.validate_device_id(mutated))

    def test_identity_is_stable_across_calls(self):
        from pythia.core.sync import identity

        first = identity.get_or_create_identity("Living Room PC")
        second = identity.get_or_create_identity("Different name, should be ignored")
        self.assertEqual(first["device_id"], second["device_id"])
        self.assertEqual(first["system_uuid"], second["system_uuid"])
        self.assertEqual(first["certificate_fingerprint"], second["certificate_fingerprint"])

    def test_rename_keeps_device_id(self):
        from pythia.core.sync import identity

        original = identity.get_or_create_identity("Original Name")
        renamed = identity.set_system_name("New Name")
        self.assertEqual(original["device_id"], renamed["device_id"])
        self.assertEqual("New Name", renamed["system_name"])

    def test_cert_and_key_files_written(self):
        from pythia.core.sync import identity

        record = identity.get_or_create_identity("Test System")
        from pathlib import Path

        self.assertTrue(Path(record["certificate_path"]).is_file())
        self.assertTrue(Path(record["private_key_path"]).is_file())


class SyncTrustAndPairingTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        os.environ["PYTHIA_DATA_DIR"] = self._tmpdir.name

    def tearDown(self):
        self._tmpdir.cleanup()
        os.environ.pop("PYTHIA_DATA_DIR", None)

    def test_pairing_roundtrip_adds_trust(self):
        from pythia.core.sync import identity, pairing, trust

        # Simulate a second system by building a package with a different
        # system_uuid than this process's own (get_or_create_identity() is
        # process-local, so we hand-construct the "remote" side's package
        # instead of spinning up a second isolated process).
        remote_uuid = "11111111-1111-1111-1111-111111111111"
        package = pairing.create_pairing_package(friendly_name="Steam Deck")
        package["system_uuid"] = remote_uuid
        # Re-derive device_id/fingerprint consistency isn't required here --
        # accept_pairing_package only checks the device ID's own check
        # digits, not that it matches system_uuid (that binding is asserted
        # by the sender, verified later at real connection time).

        self.assertFalse(trust.is_trusted(remote_uuid))
        record = pairing.accept_pairing_package(package)
        self.assertTrue(trust.is_trusted(remote_uuid))
        self.assertEqual("Steam Deck", record["friendly_name"])

        fetched = trust.get_trusted(remote_uuid)
        self.assertEqual(package["device_id"], fetched["device_id"])

    def test_expired_package_rejected(self):
        from pythia.core.sync import pairing

        package = pairing.create_pairing_package(ttl_seconds=-10)
        self.assertTrue(pairing.is_expired(package))
        with self.assertRaises(ValueError):
            pairing.accept_pairing_package(package)

    def test_self_pairing_rejected(self):
        from pythia.core.sync import identity, pairing

        package = pairing.create_pairing_package()
        self.assertEqual(identity.get_system_uuid(), package["system_uuid"])
        with self.assertRaises(ValueError):
            pairing.accept_pairing_package(package)

    def test_corrupted_device_id_rejected(self):
        from pythia.core.sync import pairing

        package = pairing.create_pairing_package()
        package["system_uuid"] = "22222222-2222-2222-2222-222222222222"
        package["device_id"] = "AAAAAAB-AAAAAAA-AAAAAAA-AAAAAAA-AAAAAAA-AAAAAAA-AAAAAAA-AAAAAAA"
        with self.assertRaises(ValueError):
            pairing.accept_pairing_package(package)

    def test_text_encoding_roundtrip(self):
        from pythia.core.sync import pairing

        package = pairing.create_pairing_package(friendly_name="Handheld")
        text = pairing.encode_pairing_text(package)
        self.assertTrue(text.startswith("pythia-pair:"))
        decoded = pairing.decode_pairing_text(text)
        self.assertEqual(package, decoded)

    def test_manual_entry_pairing(self):
        from pythia.core.sync import identity, pairing, trust

        remote_record_device_id = identity.device_id_from_der(b"fake-cert-bytes-for-test-purposes-only")
        remote_uuid = "33333333-3333-3333-3333-333333333333"
        result = pairing.pair_by_manual_entry(
            system_uuid=remote_uuid, device_id=remote_record_device_id, friendly_name="Manually Entered"
        )
        self.assertEqual("", result["certificate_fingerprint"])
        self.assertTrue(trust.is_trusted(remote_uuid))

    def test_manual_entry_rejects_bad_device_id(self):
        from pythia.core.sync import pairing

        with self.assertRaises(ValueError):
            pairing.pair_by_manual_entry(
                system_uuid="44444444-4444-4444-4444-444444444444",
                device_id="NOTREAL-NOTREAL-NOTREAL-NOTREAL-NOTREAL-NOTREAL-NOTREAL-NOTREAL",
                friendly_name="Bad",
            )


if __name__ == "__main__":
    unittest.main()
