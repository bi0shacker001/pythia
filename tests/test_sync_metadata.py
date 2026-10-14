import unittest

from pythia.core.sync.metadata import MetadataError, create_record, create_tombstone, reconcile, summarize, validate_item


class SyncMetadataTests(unittest.TestCase):
    def record(self, name, revision=1):
        return create_record({"system_uuid": "peer", "game_id": "game", "canonical_name": name}, revision=revision, updated_at=10)

    def test_newer_record_and_tombstone_are_applied(self):
        old = self.record("Old", 1)
        new = self.record("New", 2)
        result = reconcile([old], [new], peer_system_uuid="peer")
        self.assertEqual(result["upserts"], [new])
        tombstone = create_tombstone("peer", "game", 3, deleted_at=20)
        result = reconcile([new], [tombstone], peer_system_uuid="peer")
        self.assertEqual(result["tombstones"], [tombstone])

    def test_equal_revision_divergence_is_conflict_not_last_writer_wins(self):
        result = reconcile([self.record("A", 5)], [self.record("B", 5)], peer_system_uuid="peer")
        self.assertEqual(len(result["conflicts"]), 1)
        self.assertFalse(result["upserts"])

    def test_peer_cannot_publish_another_systems_record(self):
        forged = create_record({"system_uuid": "victim", "game_id": "game"}, revision=1)
        with self.assertRaises(MetadataError):
            reconcile([], [forged], peer_system_uuid="attacker")

    def test_payload_tampering_and_duplicates_are_rejected(self):
        record = self.record("Good")
        record["payload"]["canonical_name"] = "Tampered"
        with self.assertRaises(MetadataError):
            validate_item(record)
        valid = self.record("Good")
        with self.assertRaises(MetadataError):
            summarize([valid, valid])


if __name__ == "__main__":
    unittest.main()
