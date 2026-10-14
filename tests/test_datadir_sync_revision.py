"""Real tests for pythia.datadir's sync_revision counter and tombstone
persistence (2026-08-08, "optional core sync service and native
device/transfer workflow" -- "Add real revision/tombstone persistence if
missing"). Isolated via PYTHIA_DATA_DIR, same lesson-learned pattern as
tests/test_download_automator.py.
"""

import os
import tempfile
import unittest


class DatadirSyncRevisionTests(unittest.TestCase):
    def setUp(self):
        self._data_dir = tempfile.TemporaryDirectory()
        self._prior_data_dir = os.environ.get("PYTHIA_DATA_DIR")
        os.environ["PYTHIA_DATA_DIR"] = self._data_dir.name

        import importlib

        import pythia.datadir as datadir
        import pythia.identity as identity

        importlib.reload(identity)
        importlib.reload(datadir)
        self.datadir = datadir
        self.identity = identity
        self.own_uuid = identity.get_system_uuid()

    def tearDown(self):
        if self._prior_data_dir is None:
            os.environ.pop("PYTHIA_DATA_DIR", None)
        else:
            os.environ["PYTHIA_DATA_DIR"] = self._prior_data_dir
        self._data_dir.cleanup()

    def _base_entry(self, game_id="g1"):
        return {
            "system_uuid": self.own_uuid,
            "game_id": game_id,
            "canonical_name": "Test Game",
            "versions": [],
        }

    def test_save_entry_increments_sync_revision(self):
        entry = self._base_entry()
        self.datadir.save_entry(entry)
        self.assertEqual(1, entry["sync_revision"])
        entry["canonical_name"] = "Test Game Renamed"
        self.datadir.save_entry(entry)
        self.assertEqual(2, entry["sync_revision"])

    def test_remove_own_entry_records_a_real_tombstone(self):
        entry = self._base_entry("g2")
        self.datadir.save_entry(entry)
        self.assertEqual([], self.datadir.list_tombstones())

        self.assertTrue(self.datadir.remove_entry(self.own_uuid, "g2"))
        tombstones = self.datadir.list_tombstones()
        self.assertEqual(1, len(tombstones))
        self.assertEqual(self.own_uuid, tombstones[0]["system_uuid"])
        self.assertEqual("g2", tombstones[0]["game_id"])
        self.assertEqual(2, tombstones[0]["revision"])  # 1 from save, +1 for the tombstone

    def test_remove_foreign_entry_records_no_tombstone(self):
        """A mirrored copy of another system's game being removed locally is
        not a real deletion of THAT system's record -- no tombstone belongs
        to us for data we don't own."""
        foreign_uuid = "some-other-system"
        entry = {
            "system_uuid": foreign_uuid,
            "game_id": "g3",
            "canonical_name": "Foreign Game",
            "versions": [],
        }
        # Bypass save_entry()'s own-system assumption by writing directly,
        # the same way a real synced foreign file would arrive on disk.
        import json as _json

        path = self.datadir.datadir_path()
        path.mkdir(parents=True, exist_ok=True)
        (path / self.datadir._filename(foreign_uuid, "g3", "Foreign Game")).write_text(
            _json.dumps(entry), encoding="utf-8"
        )

        self.assertTrue(self.datadir.remove_entry(foreign_uuid, "g3"))
        self.assertEqual([], self.datadir.list_tombstones())

    def test_remove_nonexistent_entry_returns_false_and_no_tombstone(self):
        self.assertFalse(self.datadir.remove_entry(self.own_uuid, "does-not-exist"))
        self.assertEqual([], self.datadir.list_tombstones())

    def test_save_mirrored_entry_pins_revision_instead_of_incrementing(self):
        """The exact bug Codex's review found: save_entry() always
        increments sync_revision, which is correct for OUR OWN writes but
        wrong for a peer's mirrored record -- it must be pinned to the
        peer's real, authoritative revision instead."""
        peer_uuid = "peer-system"
        payload = {"system_uuid": peer_uuid, "game_id": "g1", "canonical_name": "Peer Game", "versions": []}

        self.datadir.save_mirrored_entry(dict(payload), revision=7)
        mirrored = self.datadir.list_mirrored_records(peer_uuid)
        self.assertEqual(1, len(mirrored))
        self.assertEqual(7, mirrored[0]["sync_revision"])

        # Re-mirroring at a LOWER revision would be a caller bug (the caller
        # should only ever call this after reconcile() classified it as a
        # real upsert), but the pinning behavior itself must hold regardless.
        self.datadir.save_mirrored_entry(dict(payload), revision=12)
        mirrored = self.datadir.list_mirrored_records(peer_uuid)
        self.assertEqual(1, len(mirrored))
        self.assertEqual(12, mirrored[0]["sync_revision"])

    def test_list_mirrored_records_only_returns_that_owner(self):
        self.datadir.save_mirrored_entry(
            {"system_uuid": "peer-a", "game_id": "g1", "canonical_name": "A's Game", "versions": []}, revision=1
        )
        self.datadir.save_mirrored_entry(
            {"system_uuid": "peer-b", "game_id": "g2", "canonical_name": "B's Game", "versions": []}, revision=1
        )
        own_entry = self._base_entry("g3")
        self.datadir.save_entry(own_entry)

        peer_a_only = self.datadir.list_mirrored_records("peer-a")
        self.assertEqual(1, len(peer_a_only))
        self.assertEqual("g1", peer_a_only[0]["game_id"])
        # Our own entry must never leak into another owner's mirrored set.
        self.assertFalse(any(e["game_id"] == "g3" for e in peer_a_only))

    def test_apply_mirrored_tombstone_deletes_file_and_persists_revision(self):
        peer_uuid = "peer-system"
        self.datadir.save_mirrored_entry(
            {"system_uuid": peer_uuid, "game_id": "g1", "canonical_name": "Peer Game", "versions": []}, revision=3
        )
        self.assertEqual(1, len(self.datadir.list_mirrored_records(peer_uuid)))

        self.datadir.apply_mirrored_tombstone(peer_uuid, "g1", 4, "2026-08-08T00:00:00+00:00")
        self.assertEqual(0, len(self.datadir.list_mirrored_records(peer_uuid)))
        tombstones = self.datadir.list_mirrored_tombstones(peer_uuid)
        self.assertEqual(1, len(tombstones))
        self.assertEqual(4, tombstones[0]["revision"])

    def test_mirrored_tombstone_persists_across_a_real_process_restart(self):
        """Restart persistence, the literal requirement from the review:
        reload both modules (simulating a fresh process against the same
        on-disk datadir) and confirm the mirrored tombstone -- and its
        revision -- survives, so a later stale push is still rejectable."""
        import importlib

        peer_uuid = "peer-system"
        self.datadir.save_mirrored_entry(
            {"system_uuid": peer_uuid, "game_id": "g1", "canonical_name": "Peer Game", "versions": []}, revision=3
        )
        self.datadir.apply_mirrored_tombstone(peer_uuid, "g1", 4, "2026-08-08T00:00:00+00:00")

        importlib.reload(self.identity)
        importlib.reload(self.datadir)

        self.assertEqual(0, len(self.datadir.list_mirrored_records(peer_uuid)))
        tombstones = self.datadir.list_mirrored_tombstones(peer_uuid)
        self.assertEqual(1, len(tombstones))
        self.assertEqual(4, tombstones[0]["revision"])

    def test_mirrored_entry_revision_persists_across_a_real_process_restart(self):
        import importlib

        peer_uuid = "peer-system"
        self.datadir.save_mirrored_entry(
            {"system_uuid": peer_uuid, "game_id": "g1", "canonical_name": "Peer Game", "versions": []}, revision=9
        )

        importlib.reload(self.identity)
        importlib.reload(self.datadir)

        mirrored = self.datadir.list_mirrored_records(peer_uuid)
        self.assertEqual(1, len(mirrored))
        self.assertEqual(9, mirrored[0]["sync_revision"])


if __name__ == "__main__":
    unittest.main()
