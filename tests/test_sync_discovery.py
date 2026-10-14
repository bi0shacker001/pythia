import json
import tempfile
import unittest
from pathlib import Path

from pythia.core.sync import discovery, trust
from pythia.core.sync.connectivity.direct_tcp import load_local_node


class SyncDiscoveryTests(unittest.TestCase):
    def test_trusted_signed_announcement_round_trip(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            a_dir, b_dir = root / "a", root / "b"
            a = load_local_node("A", base_dir=a_dir)
            b = load_local_node("B", base_dir=b_dir)
            trust.add_trusted(system_uuid=a.system_uuid, device_id=a.device_id,
                              certificate_fingerprint=a.identity_record["certificate_fingerprint"],
                              friendly_name="A", base_dir=b_dir)
            packet = discovery.create_announcement(a, 22000, ["192.168.1.2", "fe80::1"], now=1000)
            result = discovery.verify_announcement(packet, base_dir=b_dir, now=1001)
            self.assertEqual(result["system_uuid"], a.system_uuid)
            self.assertEqual(result["addresses"], ["192.168.1.2", "fe80::1"])

    def test_untrusted_tampered_and_expired_packets_are_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            a_dir, b_dir = root / "a", root / "b"
            a = load_local_node("A", base_dir=a_dir)
            packet = discovery.create_announcement(a, 22000, ["127.0.0.1"], now=1000)
            with self.assertRaises(discovery.DiscoveryError):
                discovery.verify_announcement(packet, base_dir=b_dir, now=1001)
            trust.add_trusted(system_uuid=a.system_uuid, device_id=a.device_id,
                              certificate_fingerprint=a.identity_record["certificate_fingerprint"],
                              friendly_name="A", base_dir=b_dir)
            tampered = json.loads(packet)
            tampered["body"]["port"] = 22001
            with self.assertRaises(discovery.DiscoveryError):
                discovery.verify_announcement(json.dumps(tampered).encode(), base_dir=b_dir, now=1001)
            with self.assertRaises(discovery.DiscoveryError):
                discovery.verify_announcement(packet, base_dir=b_dir, now=1201)


if __name__ == "__main__":
    unittest.main()
