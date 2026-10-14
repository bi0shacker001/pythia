import tempfile
import unittest
from pathlib import Path

from pythia.core.sync import metadata, staging


class SyncStagingTests(unittest.TestCase):
    def items(self):
        return [
            metadata.create_record({"system_uuid": "system-a", "game_id": "game-a", "canonical_name": "A"}, revision=2, updated_at=10),
            metadata.create_tombstone("system-b", "game-b", 3, deleted_at=11),
        ]

    def test_atomic_snapshot_round_trip_and_content_deduplication(self):
        with tempfile.TemporaryDirectory() as td:
            first = staging.export_snapshot(self.items(), td, created_at=20)
            second = staging.export_snapshot(self.items(), td, created_at=20)
            self.assertEqual(first["snapshot_id"], second["snapshot_id"])
            loaded = staging.import_snapshot(td)
            self.assertEqual(loaded["items"], self.items())
            self.assertEqual(len(list((Path(td) / "pythia-sync" / "default" / "objects").rglob("*.json"))), 2)

    def test_corrupt_object_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            staging.export_snapshot(self.items(), td)
            object_path = next((Path(td) / "pythia-sync" / "default" / "objects").rglob("*.json"))
            object_path.write_bytes(b"corrupt")
            with self.assertRaises(staging.StagingError):
                staging.import_snapshot(td)

    def test_unsafe_repository_id_and_duplicate_item_are_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(staging.StagingError):
                staging.export_snapshot([], td, repository_id="../escape")
            item = self.items()[0]
            with self.assertRaises(staging.StagingError):
                staging.export_snapshot([item, item], td)

    def test_named_device_heads_merge_by_revision(self):
        with tempfile.TemporaryDirectory() as td:
            older = metadata.create_record(
                {"system_uuid": "system-a", "game_id": "game-a", "canonical_name": "Old"}, revision=1, updated_at=1
            )
            newer = metadata.create_record(
                {"system_uuid": "system-a", "game_id": "game-a", "canonical_name": "New"}, revision=2, updated_at=2
            )
            staging.export_snapshot([older], td, writer_id="device-a")
            staging.export_snapshot([newer], td, writer_id="device-b")
            merged = staging.import_all_heads(td)
            self.assertEqual("ok", merged["status"])
            self.assertEqual([newer], merged["items"])
            self.assertEqual({"device-a", "device-b"}, set(merged["heads"]))

    def test_equal_revision_divergence_is_reported_and_omitted(self):
        with tempfile.TemporaryDirectory() as td:
            left = metadata.create_record(
                {"system_uuid": "system-a", "game_id": "game-a", "canonical_name": "Left"}, revision=4, updated_at=4
            )
            right = metadata.create_record(
                {"system_uuid": "system-a", "game_id": "game-a", "canonical_name": "Right"}, revision=4, updated_at=4
            )
            staging.export_snapshot([left], td, writer_id="device-a")
            staging.export_snapshot([right], td, writer_id="device-b")
            merged = staging.import_all_heads(td)
            self.assertEqual("conflict", merged["status"])
            self.assertEqual([], merged["items"])
            self.assertEqual(1, len(merged["conflicts"]))

    def test_newer_revision_resolves_an_older_divergence(self):
        with tempfile.TemporaryDirectory() as td:
            def record(name, revision):
                return metadata.create_record(
                    {"system_uuid": "system-a", "game_id": "game-a", "canonical_name": name},
                    revision=revision,
                    updated_at=revision,
                )

            staging.export_snapshot([record("Left", 4)], td, writer_id="device-a")
            staging.export_snapshot([record("Right", 4)], td, writer_id="device-b")
            latest = record("Resolved", 5)
            staging.export_snapshot([latest], td, writer_id="device-c")
            merged = staging.import_all_heads(td)
            self.assertEqual("ok", merged["status"])
            self.assertEqual([latest], merged["items"])
            self.assertEqual([], merged["conflicts"])


if __name__ == "__main__":
    unittest.main()
