"""query_games, the collections and the art bytes on the real Backend (pythia.ui.backend), over real datadir records,
and the play count where Pythia records a play. Needs PySide6 with its offscreen platform, as
tests/_test_gaming_slots.py does."""

import base64
import os
import shutil
import tempfile
import time
from pathlib import Path
from unittest import TestCase, mock, skipUnless

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtGui import QGuiApplication

    HAVE_QT = True
except ImportError:  # pragma: no cover
    HAVE_QT = False


def wait_for(app, done, seconds=30):
    deadline = time.monotonic() + seconds
    while not done() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    return done()


@skipUnless(HAVE_QT, "PySide6 is not installed")
class MoreGamingSlotsTest(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = tempfile.TemporaryDirectory(prefix="pythia-gaming2-", ignore_cleanup_errors=True)
        cls.datadir = os.path.join(cls.data.name, "dd")
        cls.env = mock.patch.dict(
            os.environ, {"PYTHIA_DATA_DIR": cls.data.name, "PYTHIA_DATADIR_OVERRIDE": cls.datadir}
        )
        cls.env.start()
        cls.app = QGuiApplication.instance() or QGuiApplication([])
        from pythia.ui.backend import Backend

        cls.backend = Backend()

    @classmethod
    def tearDownClass(cls):
        cls.env.stop()
        cls.data.cleanup()

    def setUp(self):
        from pythia import datadir, platform_systems
        from pythia.identity import get_system_uuid

        self.datadir_module = datadir
        self.me = get_system_uuid()
        for old in Path(self.datadir).glob("*.json"):
            old.unlink()
        shutil.rmtree(Path(self.datadir) / "collections", ignore_errors=True)
        subset = Path(__file__).resolve().parent / "fixtures" / "platforms-database-subset.json"
        patch = mock.patch.object(platform_systems, "PINNED", subset)
        patch.start()
        self.addCleanup(patch.stop)

    def put(self, game_id, name, variants, **fields):
        entry = {
            "system_uuid": self.me,
            "game_id": game_id,
            "canonical_name": name,
            "platform_ids": fields.pop("platform_ids", []),
            "versions": [{"version": "1.0", "variants": variants}],
        }
        entry.update(fields)
        self.datadir_module.save_entry(entry)

    def library(self):
        self.put("g1", "Moonlit Harbor", [{"id": "a", "path": "/g/m", "platform": "Windows", "year": 1999}])
        self.put("g2", "Moonlit Fields", [{"id": "b", "path": "/g/f", "platform": "Linux", "year": 2004}])
        self.put("g3", "Crash Racer", [{"id": "c", "path": "/g/c", "platform": "Sony PlayStation 2", "year": 2002}])
        self.backend.refresh_games()

    # ---- query_games ------------------------------------------------------------------------------------------------
    def test_a_query_answers_a_page_the_total_and_the_facets_with_the_games_version(self):
        self.library()
        answer = self.backend.query_games({"text": "moonlit", "sort": "year", "descending": True})
        self.assertEqual(answer["status"], "ok")
        self.assertEqual([g["id"] for g in answer["games"]], ["g2", "g1"])
        self.assertEqual(answer["total"], 2)
        self.assertEqual(answer["version"], self.backend.get_games()["version"])
        self.assertEqual({f["id"]: f["count"] for f in answer["facets"]["system"]}, {"windows": 1, "pc": 1})

    def test_no_query_and_an_empty_one_give_the_whole_library(self):
        self.library()
        for query in (None, "", {}):
            self.assertEqual(self.backend.query_games(query)["total"], 3)

    def test_a_query_the_backend_cannot_answer_is_an_error_naming_the_part(self):
        self.library()
        answer = self.backend.query_games({"sort": "colour"})
        self.assertEqual((answer["status"], answer["code"]), ("error", "bad-query"))
        self.assertIn("sort", answer["message"])
        answer = self.backend.query_games(["a list"])
        self.assertEqual((answer["status"], answer["code"]), ("error", "bad-query"))

    # ---- collections ------------------------------------------------------------------------------------------------
    def test_a_collection_is_made_filled_queried_renamed_and_deleted_and_each_change_is_told(self):
        self.library()
        told: list = []
        self.backend.collectionsChanged.connect(told.append)
        made = self.backend.create_collection("Co-op")
        self.assertEqual((made["status"], made["collection"]["name"]), ("ok", "Co-op"))
        cid = made["collection"]["id"]
        added = self.backend.add_to_collection(cid, ["g1", "g3", "nope"])
        self.assertEqual((added["status"], added["changed"], added["unknown"]), ("ok", ["g1", "g3"], ["nope"]))
        self.assertEqual(added["collection"]["game_ids"], ["g1", "g3"])
        listed = self.backend.get_collections()
        self.assertEqual([c["name"] for c in listed["collections"]], ["Co-op"])
        found = self.backend.query_games({"collection": cid})
        self.assertEqual([g["id"] for g in found["games"]], ["g3", "g1"])
        removed = self.backend.remove_from_collection(cid, ["g1"])
        self.assertEqual((removed["changed"], removed["collection"]["game_ids"]), (["g1"], ["g3"]))
        self.assertEqual(self.backend.rename_collection(cid, "Couch")["collection"]["name"], "Couch")
        self.assertEqual(self.backend.delete_collection(cid)["status"], "ok")
        self.assertEqual(self.backend.get_collections()["collections"], [])
        self.assertEqual(
            [(t["kind"], t["id"]) for t in told],
            [("created", cid), ("members", cid), ("members", cid), ("renamed", cid), ("deleted", cid)],
        )
        self.assertEqual(told[-1]["version"], self.backend.get_collections()["version"])

    def test_the_refusals_have_their_codes(self):
        self.library()
        made = self.backend.create_collection("One")["collection"]["id"]
        self.assertEqual(self.backend.create_collection("one")["code"], "name-taken")
        self.assertEqual(self.backend.create_collection("")["code"], "empty-name")
        self.assertEqual(self.backend.rename_collection("nope", "X")["code"], "no-such-collection")
        self.assertEqual(self.backend.add_to_collection("nope", ["g1"])["code"], "no-such-collection")
        self.assertEqual(self.backend.delete_collection("nope")["code"], "no-such-collection")
        self.assertEqual(self.backend.query_games({"collection": "nope"})["code"], "no-such-collection")
        self.assertEqual(self.backend.add_to_collection(made, "not a list")["changed"], [])

    def test_a_collection_another_computer_changed_is_told_when_the_library_is_read_again(self):
        from pythia import game_collections

        self.library()
        cid = self.backend.create_collection("Mine")["collection"]["id"]
        told: list = []
        self.backend.collectionsChanged.connect(told.append)
        other = "00000000-0000-4000-8000-0000000000ee"
        with mock.patch.object(game_collections, "get_system_uuid", return_value=other):
            game_collections.add_games(cid, ["g2"])
        self.backend.refresh_games()
        self.assertEqual([t["kind"] for t in told], ["synced"])
        self.backend.refresh_games()
        self.assertEqual(len(told), 1)

    # ---- the art as bytes -------------------------------------------------------------------------------------------
    def test_the_art_is_read_in_chunks_a_client_on_another_computer_can_put_together(self):
        from pythia import covers

        art = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, art, True)
        payload = bytes(range(256)) * 10  # 2560 bytes
        (art / "moonlit-harbor.jpg").write_bytes(payload)
        self.library()
        with mock.patch.object(covers.settings, "COVERART_PATH", str(art)):
            settled: list = []
            self.backend.asyncResult.connect(settled.append)

            def chunk(offset, length):
                pending = self.backend.get_game_art_data("g1", "cover", offset, length)
                self.assertEqual(pending["status"], "pending")
                self.assertTrue(wait_for(self.app, lambda: any(s["request"] == pending["request"] for s in settled)))
                return next(s for s in settled if s["request"] == pending["request"])

            got = b""
            offset = 0
            while True:
                part = chunk(offset, 1000)
                self.assertEqual((part["status"], part["mime"], part["size"]), ("ok", "image/jpeg", 2560))
                got += base64.b64decode(part["data"])
                offset += part["length"]
                if part["eof"]:
                    break
            self.assertEqual(got, payload)
            self.assertEqual(offset, 2560)
            self.assertEqual(chunk(2560, 10)["length"], 0)
            self.assertEqual(chunk(9999, 10)["code"], "bad-range")

    def test_art_that_is_not_there_and_a_game_that_is_not_there_are_told_apart(self):
        self.library()
        settled: list = []
        self.backend.asyncResult.connect(settled.append)
        pending = self.backend.get_game_art_data("g1", "screenshot", 0, 100)
        self.assertTrue(wait_for(self.app, lambda: any(s["request"] == pending["request"] for s in settled)))
        self.assertEqual(next(s for s in settled if s["request"] == pending["request"])["status"], "unavailable")
        pending = self.backend.get_game_art_data("nope", "cover", 0, 100)
        self.assertTrue(wait_for(self.app, lambda: any(s["request"] == pending["request"] for s in settled)))
        self.assertEqual(next(s for s in settled if s["request"] == pending["request"])["code"], "no-such-game")

    # ---- the play count ---------------------------------------------------------------------------------------------
    def test_the_play_count_is_kept_with_the_variant_and_in_the_index_and_shows_on_the_card_and_detail(self):
        from pythia.database import games as games_db
        from pythia.database import schema

        schema.syncdb()
        self.put("g9", "Played Twice", [{"id": "z", "path": "/g/z", "platform": "Linux", "playcount": 1}])
        entry = self.datadir_module.list_known_games()["g9"][0]
        games_db.save_entry_and_sync_cache(entry)
        games_db.update_existing(id="g9", playcount=2)
        kept = self.datadir_module.list_known_games()["g9"][0]
        self.assertEqual(kept["versions"][0]["variants"][0]["playcount"], 2)
        self.assertEqual(games_db.get_game_by_field("g9", "id")["playcount"], 2)  # the row is read from the record
        self.backend.refresh_games()
        card = next(c for c in self.backend.get_games()["games"] if c["id"] == "g9")
        self.assertEqual(card["playcount"], 2)
        self.assertEqual(self.backend.get_game_detail("g9")["fields"]["playcount"], 2)
