"""The person's collections (pythia.game_collections): kept in the library folder, one file per computer, merged by
the newest change, over a datadir of its own."""

import json
import os
import tempfile
from pathlib import Path
from unittest import TestCase, mock

from pythia import game_collections as collections_module
from pythia.identity import get_system_uuid
from pythia.util.test_config import setup_test_environment

setup_test_environment()

OTHER = "00000000-0000-4000-8000-0000000000dd"


class CollectionsCase(TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        patch = mock.patch.dict(os.environ, {"PYTHIA_DATADIR_OVERRIDE": self.folder})
        patch.start()
        self.addCleanup(patch.stop)
        self.me = get_system_uuid()
        self.clock = 0
        stamp = mock.patch.object(collections_module, "now", side_effect=self.tick)
        stamp.start()
        self.addCleanup(stamp.stop)

    def tick(self):
        self.clock += 1
        return f"2026-11-24T10:00:{self.clock:02d}.000+00:00"

    def as_computer(self, system_uuid):
        """Changes made as another computer: its own file, written by it."""
        return mock.patch.object(collections_module, "get_system_uuid", return_value=system_uuid)

    def names(self):
        return [c["name"] for c in collections_module.merged()]


class CollectionsTest(CollectionsCase):
    def test_a_new_collection_is_empty_and_kept_in_this_computers_own_file(self):
        made = collections_module.create("  Co-op   games ")
        self.assertEqual((made["name"], made["game_ids"], made["count"]), ("Co-op games", [], 0))
        self.assertEqual(collections_module.merged(), [made])
        path = Path(self.folder) / "collections" / f"{self.me}.json"
        self.assertTrue(path.is_file())
        self.assertEqual(json.loads(path.read_text())["system_uuid"], self.me)

    def test_the_games_readers_never_see_the_collection_files(self):
        from pythia import datadir

        collections_module.create("Co-op")
        self.assertEqual(datadir.list_all_entries(), [])
        self.assertEqual(list(Path(self.folder).glob("*.json")), [])

    def test_games_are_added_and_removed_and_only_what_changed_is_told(self):
        made = collections_module.create("To finish")
        after, changed = collections_module.add_games(made["id"], ["g2", "g1", "g1"])
        self.assertEqual((after["game_ids"], changed), (["g1", "g2"], ["g2", "g1"]))
        again, none = collections_module.add_games(made["id"], ["g1"])
        self.assertEqual((again["game_ids"], none), (["g1", "g2"], []))
        left, removed = collections_module.remove_games(made["id"], ["g1", "g9"])
        self.assertEqual((left["game_ids"], removed, left["count"]), (["g2"], ["g1"], 1))

    def test_rename_and_delete_and_the_names_are_unique_ignoring_case(self):
        one = collections_module.create("One")
        two = collections_module.create("Two")
        self.assertEqual(collections_module.rename(one["id"], "Uno")["name"], "Uno")
        self.assertEqual(self.names(), ["Two", "Uno"])
        with self.assertRaises(collections_module.CollectionError) as taken:
            collections_module.rename(two["id"], "uno")
        self.assertEqual(taken.exception.code, "name-taken")
        collections_module.rename(two["id"], "TWO")  # its own name in another case is allowed
        collections_module.delete(one["id"])
        self.assertEqual(self.names(), ["TWO"])
        with self.assertRaises(collections_module.CollectionError) as gone:
            collections_module.add_games(one["id"], ["g1"])
        self.assertEqual(gone.exception.code, "no-such-collection")
        # The name of a deleted collection is free again.
        self.assertEqual(collections_module.create("Uno")["name"], "Uno")

    def test_a_name_must_be_there_and_not_too_long(self):
        for bad, code in (("", "empty-name"), ("   ", "empty-name"), ("x" * 101, "name-too-long")):
            with self.assertRaises(collections_module.CollectionError) as caught:
                collections_module.create(bad)
            self.assertEqual(caught.exception.code, code)

    def test_the_version_changes_with_a_name_a_member_or_a_deletion(self):
        made = collections_module.create("One")
        seen = [collections_module.version()]
        collections_module.add_games(made["id"], ["g1"])
        seen.append(collections_module.version())
        collections_module.rename(made["id"], "Uno")
        seen.append(collections_module.version())
        collections_module.delete(made["id"])
        seen.append(collections_module.version())
        self.assertEqual(len(set(seen)), 4)


class TwoComputersTest(CollectionsCase):
    def test_a_collection_made_on_another_computer_is_the_same_collection_here(self):
        with self.as_computer(OTHER):
            made = collections_module.create("Shared")
            collections_module.add_games(made["id"], ["g1"])
        here = collections_module.merged()
        self.assertEqual([(c["name"], c["game_ids"]) for c in here], [("Shared", ["g1"])])
        # Each computer wrote only its own file.
        self.assertEqual(sorted(p.name for p in (Path(self.folder) / "collections").glob("*.json")), [f"{OTHER}.json"])

    def test_the_newest_change_to_a_member_wins_whichever_computer_made_it(self):
        made = collections_module.create("Shared")
        collections_module.add_games(made["id"], ["g1", "g2"])
        with self.as_computer(OTHER):
            collections_module.remove_games(made["id"], ["g1"])
        self.assertEqual(collections_module.merged()[0]["game_ids"], ["g2"])
        collections_module.add_games(made["id"], ["g1"])  # later than the removal
        self.assertEqual(collections_module.merged()[0]["game_ids"], ["g1", "g2"])

    def test_the_newest_name_wins_and_a_deletion_on_either_computer_is_the_collections_end(self):
        made = collections_module.create("First")
        with self.as_computer(OTHER):
            collections_module.rename(made["id"], "Second")
        self.assertEqual(self.names(), ["Second"])
        collections_module.rename(made["id"], "Third")
        self.assertEqual(self.names(), ["Third"])
        with self.as_computer(OTHER):
            collections_module.delete(made["id"])
        self.assertEqual(collections_module.merged(), [])

    def test_a_file_that_cannot_be_read_is_left_out_not_fatal(self):
        collections_module.create("Mine")
        (Path(self.folder) / "collections" / f"{OTHER}.json").write_text("{not json")
        self.assertEqual(self.names(), ["Mine"])
