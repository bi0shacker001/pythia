"""The gaming front end's slots on the real Backend (pythia.ui.backend), over real datadir records: the cards, the
systems, a game's detail, its art, the running games, quitting one, and cancelling a job. Needs PySide6 with its
offscreen platform, as tests/_test_ipc_backend.py does."""

import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest import TestCase, mock, skipUnless

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtGui import QGuiApplication

    HAVE_QT = True
except ImportError:  # pragma: no cover
    HAVE_QT = False

OTHER = "00000000-0000-4000-8000-0000000000cc"


def wait_for(app, done, seconds=30):
    deadline = time.monotonic() + seconds
    while not done() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    return done()


@skipUnless(HAVE_QT, "PySide6 is not installed")
class GamingSlotsTest(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = tempfile.TemporaryDirectory(prefix="pythia-gaming-", ignore_cleanup_errors=True)
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
        from pythia import datadir
        from pythia.identity import get_system_uuid

        self.datadir_module = datadir
        self.me = get_system_uuid()
        for old in Path(self.datadir).glob("*.json"):
            old.unlink()
        self.addCleanup(self.backend._jobs.clear)

    def put(self, game_id, name, variants, system=None, **fields):
        entry = {
            "system_uuid": system or self.me,
            "game_id": game_id,
            "canonical_name": name,
            "platform_ids": fields.pop("platform_ids", []),
            "versions": [{"version": "1.0", "variants": variants}],
        }
        entry.update(fields)
        self.datadir_module.save_entry(entry)

    def reread(self):
        self.backend.refresh_games()

    # ---- the list -------------------------------------------------------------------------------------------------
    def test_one_call_answers_every_card_and_a_client_with_the_version_is_told_nothing_changed(self):
        self.put("g1", "Moonlit Harbor", [{"id": "a", "path": "/g/m", "platform": "Linux", "playtime": 2.0}])
        self.put("g2", "Celeste", [{"id": "b", "platform": "Windows"}], system=OTHER)
        self.reread()
        answer = self.backend.get_games()
        self.assertEqual(answer["status"], "ok")
        cards = {c["id"]: c for c in answer["games"]}
        self.assertEqual(sorted(cards), ["g1", "g2"])
        self.assertEqual((cards["g1"]["title"], cards["g1"]["installed"]), ("Moonlit Harbor", True))
        self.assertEqual((cards["g1"]["playtime_hours"], cards["g2"]["installed"]), (2.0, False))
        same = self.backend.get_games(answer["version"])
        self.assertEqual((same["status"], same["unchanged"], same["games"]), ("ok", True, []))
        self.assertEqual(self.backend.get_games("another")["games"], answer["games"])

    def test_reading_the_library_again_tells_the_new_version(self):
        self.put("g1", "One", [{"id": "a", "path": "/g/1", "platform": "Linux"}])
        self.reread()
        told: list = []
        changed: list = []
        self.backend.gamesVersionChanged.connect(told.append)
        self.backend.gamesChanged.connect(lambda: changed.append(True))
        before = self.backend.get_games()["version"]
        self.put("g2", "Two", [{"id": "b", "path": "/g/2", "platform": "Linux"}])
        self.reread()
        after = self.backend.get_games()["version"]
        self.assertNotEqual(before, after)
        self.assertEqual(told, [after])
        self.assertEqual(changed, [True])

    def test_the_systems_are_counted_from_the_same_snapshot(self):
        self.put("g1", "One", [{"id": "a", "path": "/g/1", "platform": "Linux"}])
        self.put("g2", "Two", [{"id": "b", "path": "/g/2", "platform": "Linux"}, {"id": "c", "platform": "Windows"}])
        self.reread()
        answer = self.backend.get_game_systems()
        self.assertEqual(answer["status"], "ok")
        self.assertEqual({s["name"]: s["count"] for s in answer["systems"]}, {"Linux": 2, "Windows": 1})
        self.assertEqual(answer["version"], self.backend.get_games()["version"])

    # ---- one game ---------------------------------------------------------------------------------------------------
    def test_a_detail_says_what_it_has_and_what_it_cannot_give(self):
        self.put("g1", "Ironvale", [{"id": "a", "path": "/g/i", "platform": "Linux", "year": 2021}], notes="chapter 2")
        self.reread()
        answer = self.backend.get_game_detail("g1")
        self.assertEqual(answer["status"], "ok")
        self.assertEqual((answer["fields"]["title"], answer["fields"]["year"]), ("Ironvale", 2021))
        self.assertEqual(sorted(answer["missing"]), ["description", "developer", "genre", "publisher", "rating"])
        self.assertEqual(answer["missing"]["genre"]["status"], "unavailable")

    def test_a_game_that_is_not_in_the_library_is_an_error_not_an_empty_page(self):
        self.reread()
        answer = self.backend.get_game_detail("nope")
        self.assertEqual((answer["status"], answer["code"]), ("error", "no-such-game"))

    def test_art_answers_later_with_a_file_or_the_reason_there_is_none(self):
        from pythia import covers

        art = Path(tempfile.mkdtemp())
        (art / "ironvale.jpg").write_bytes(b"jpg")
        self.put("g1", "Ironvale", [{"id": "a", "path": "/g/i", "platform": "Linux"}])
        with mock.patch.object(covers.settings, "COVERART_PATH", str(art)):
            self.reread()
            settled: list = []
            self.backend.asyncResult.connect(settled.append)
            pending = self.backend.get_game_art("g1", "cover")
            self.assertEqual(pending["status"], "pending")
            self.assertTrue(wait_for(self.app, lambda: any(s["request"] == pending["request"] for s in settled)))
            cover = next(s for s in settled if s["request"] == pending["request"])
            self.assertEqual((cover["status"], cover["role"], cover["mime"]), ("ok", "cover", "image/jpeg"))
            self.assertEqual(Path(cover["path"]), art / "ironvale.jpg")
            shot = self.backend.get_game_art("g1", "screenshot")
            self.assertTrue(wait_for(self.app, lambda: any(s["request"] == shot["request"] for s in settled)))
            gone = next(s for s in settled if s["request"] == shot["request"])
            self.assertEqual(gone["status"], "unavailable")

    # ---- running games ----------------------------------------------------------------------------------------------
    def start_game(self, game_id):
        import subprocess

        from pythia import running

        kwargs = {} if os.name == "nt" else {"start_new_session": True}
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], **kwargs)  # noqa: S603
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        running.TRACKER.track(game_id, proc, "folder")
        return proc

    def test_a_started_game_is_told_listed_and_ended_on_request(self):
        started: list = []
        stopped: list = []
        self.backend.gameStarted.connect(started.append)
        self.backend.gameStopped.connect(stopped.append)
        proc = self.start_game("gx")
        self.assertTrue(wait_for(self.app, lambda: bool(started)))
        self.assertEqual((started[0]["game_id"], started[0]["via"], started[0]["pid"]), ("gx", "folder", proc.pid))
        listed = self.backend.get_running_games()
        self.assertEqual(listed["status"], "ok")
        self.assertEqual([g["game_id"] for g in listed["games"]], ["gx"])
        asked = self.backend.quit_game("gx")
        self.assertEqual((asked["status"], asked["asked"]), ("ok", True))
        self.assertTrue(wait_for(self.app, lambda: bool(stopped)))
        self.assertEqual(stopped[0]["game_id"], "gx")
        self.assertEqual(self.backend.get_running_games()["games"], [])

    def test_quitting_a_game_pythia_did_not_start_is_unavailable_with_the_reason(self):
        from pythia import running

        answer = self.backend.quit_game("started-by-steam")
        self.assertEqual(answer["status"], "unavailable")
        self.assertEqual(answer["message"], running.NOT_STARTED_HERE)

    # ---- jobs -------------------------------------------------------------------------------------------------------
    def test_only_the_folder_watch_can_be_cancelled(self):
        scan = self.backend._job_start("scan", "Scanning")
        refused = self.backend.cancel_job(scan)
        self.assertEqual(refused["status"], "unavailable")
        self.assertIn("scan", refused["message"])
        watch = self.backend._job_start("watch", "Watching")
        self.backend._watcher = {"job": watch}
        self.backend._watch_stop_event = threading.Event()
        try:
            stopped = self.backend.cancel_job(watch)
            self.assertEqual((stopped["status"], stopped["stopping"]), ("ok", True))
            self.assertTrue(self.backend._watch_stop_event.is_set())
        finally:
            self.backend._watcher = None
            self.backend._watch_stop_event = None

    def test_a_finished_job_and_an_unknown_one_are_told_apart(self):
        done = self.backend._job_start("scan", "Scanning")
        self.backend._job_finish(done, "Scanned")
        self.assertEqual(self.backend.cancel_job(done)["status"], "unavailable")
        missing = self.backend.cancel_job("no-such-job")
        self.assertEqual((missing["status"], missing["code"]), ("error", "no-such-job"))
