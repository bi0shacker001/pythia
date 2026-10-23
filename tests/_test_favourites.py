"""A favourite is the person's (pythia.favourites; owner, 2026-10-23): each
computer's record keeps the mark it last saw, the newest across a game's
records wins, cache.db indexes it, and droidtop's favourite mark travels both
ways."""

import datetime
import os
import tempfile
from unittest import TestCase, mock

from pythia import datadir, favourites, settings
from pythia.database import games as games_db
from pythia.database import schema
from pythia.identity import get_system_uuid
from pythia.util.test_config import setup_test_environment

setup_test_environment()

OTHER = "00000000-0000-4000-8000-0000000000aa"


def record(system_uuid: str, game_id: str, name: str, favourite: dict | None = None, steam: str = "") -> dict:
    entry: dict = {"system_uuid": system_uuid, "game_id": game_id, "canonical_name": name, "versions": []}
    entry["platform_ids"] = [{"platform": "steam", "id": steam}] if steam else []
    if favourite is not None:
        entry["favourite"] = favourite
    return entry


class FavouritesTest(TestCase):
    def setUp(self):
        folder = tempfile.mkdtemp()
        patch = mock.patch.dict(os.environ, {"PYTHIA_DATADIR_OVERRIDE": folder})
        patch.start()
        self.addCleanup(patch.stop)
        if os.path.exists(settings.DB_PATH):
            os.remove(settings.DB_PATH)
        schema.syncdb()
        self.me = get_system_uuid()
        # No droidtop module in these tests unless one says so.
        self.native = mock.patch("pythia.droidtop.native.submodule", side_effect=self.no_module)
        self.submodule = self.native.start()
        self.addCleanup(self.native.stop)

    @staticmethod
    def no_module(name):
        from pythia.droidtop.native import AgentError

        raise AgentError("not built")

    def save(self, entry):
        games_db.save_entry_and_sync_cache(entry)

    def test_the_newest_mark_across_computers_wins(self):
        self.save(record(self.me, "g1", "Hades", {"on": True, "at": "2026-10-20T10:00:00.000+00:00"}))
        self.save(record(OTHER, "g1", "Hades", {"on": False, "at": "2026-10-21T10:00:00.000+00:00"}))
        entries = datadir.list_known_games()["g1"]
        self.assertFalse(favourites.is_favourite(entries))
        self.assertEqual(games_db.get_favourite_game_ids(), set())
        favourites.set_favourite("g1", True)
        self.assertTrue(favourites.is_favourite(datadir.list_known_games()["g1"]))
        self.assertEqual(games_db.get_favourite_game_ids(), {"g1"})

    def test_a_game_known_only_from_another_computer_gets_a_record_here(self):
        self.save(record(OTHER, "g2", "Celeste", steam="504230"))
        self.assertEqual(favourites.set_favourite("g2", True)["favourite"], True)
        mine = [e for e in datadir.list_known_games()["g2"] if e["system_uuid"] == self.me]
        self.assertEqual(len(mine), 1)
        self.assertEqual((mine[0]["canonical_name"], mine[0]["versions"]), ("Celeste", []))
        self.assertEqual(mine[0]["platform_ids"], [{"platform": "steam", "id": "504230"}])
        # The other computer's record is never written.
        theirs = [e for e in datadir.list_known_games()["g2"] if e["system_uuid"] == OTHER]
        self.assertNotIn("favourite", theirs[0])

    def test_the_index_is_rebuilt_from_the_records(self):
        self.save(record(self.me, "g3", "Tunic", {"on": True, "at": favourites.now()}))
        os.remove(settings.DB_PATH)
        schema.syncdb()
        self.assertEqual(games_db.get_favourite_game_ids(), set())
        games_db.rebuild_cache()
        self.assertEqual(games_db.get_favourite_game_ids(), {"g3"})

    def test_a_mark_made_here_is_sent_to_droidtop(self):
        store = mock.Mock()
        self.submodule.side_effect = lambda name: store
        self.save(record(self.me, "g4", "Portal 2", steam="620"))
        favourites.set_favourite("g4", True)
        store.set_mark.assert_called_once_with("steam:620", "favourite", True)

    def test_a_newer_mark_from_a_handheld_is_taken_once(self):
        self.save(record(self.me, "g5", "Portal 2", {"on": False, "at": "2026-10-20T10:00:00.000+00:00"}, steam="620"))
        store = mock.Mock()
        later = int(datetime.datetime(2026, 10, 21, tzinfo=datetime.timezone.utc).timestamp() * 1000)
        store.library.return_value = {"steam:620": {"marks": {"favourite": {"value": True, "at": {"ms": later}}}}}
        self.submodule.side_effect = lambda name: store
        self.assertEqual(favourites.take_from_handhelds(), 1)
        self.assertTrue(favourites.is_favourite(datadir.list_known_games()["g5"]))
        store.set_mark.assert_not_called()  # it came from there
        self.assertEqual(favourites.take_from_handhelds(), 0)  # nothing new the second time

    def test_an_older_mark_from_a_handheld_loses(self):
        self.save(record(self.me, "g6", "Portal 2", {"on": False, "at": "2026-10-22T10:00:00.000+00:00"}, steam="620"))
        store = mock.Mock()
        store.library.return_value = {"steam:620": {"marks": {"favourite": {"value": True, "at": {"ms": 1_000}}}}}
        self.submodule.side_effect = lambda name: store
        self.assertEqual(favourites.take_from_handhelds(), 0)
        self.assertFalse(favourites.is_favourite(datadir.list_known_games()["g6"]))
