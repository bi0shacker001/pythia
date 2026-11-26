"""Batch 3 of the gaming slots: jobs that stop between items, games running that Pythia did not start, art for every
role, and the typed result of each slot. Over datadir records written the way Pythia keeps them and the real Backend
(PySide6, offscreen), as tests/_test_gaming_slots.py does."""

import io
import os
import shutil
import tempfile
import time
from pathlib import Path
from unittest import TestCase, mock, skipUnless

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pythia import (  # noqa: E402
    gaming_api,
    look,
    onboarding,
    reconciliation,
    running,
    running_external,
    scanning,
    store_installs,
)
from pythia.ipc import results  # noqa: E402
from pythia.plugins.library import f95_updates  # noqa: E402
from pythia.util.steam.vdfutils import vdf_parse  # noqa: E402
from pythia.util.test_config import setup_test_environment  # noqa: E402
from tests._test_gaming_api import LibraryCase, record, variant  # noqa: E402

setup_test_environment()

try:
    from PySide6.QtGui import QGuiApplication

    HAVE_QT = True
except ImportError:  # pragma: no cover
    HAVE_QT = False

REGISTRY = """"Registry"
{
\t"HKCU"
\t{
\t\t"Software"
\t\t{
\t\t\t"Valve"
\t\t\t{
\t\t\t\t"Steam"
\t\t\t\t{
\t\t\t\t\t"RunningAppID"\t\t"620"
\t\t\t\t\t"Apps"
\t\t\t\t\t{
\t\t\t\t\t\t"620"
\t\t\t\t\t\t{
\t\t\t\t\t\t\t"Running"\t\t"1"
\t\t\t\t\t\t}
\t\t\t\t\t\t"730"
\t\t\t\t\t\t{
\t\t\t\t\t\t\t"Running"\t\t"0"
\t\t\t\t\t\t}
\t\t\t\t\t\t"440"
\t\t\t\t\t\t{
\t\t\t\t\t\t\t"Running"\t\t"1"
\t\t\t\t\t\t}
\t\t\t\t\t}
\t\t\t\t}
\t\t\t}
\t\t}
\t}
}
"""


def roots(count):
    return [Path(f"/g/games/{i}") for i in range(count)]


def wait_for(app, done, seconds=30):
    deadline = time.monotonic() + seconds
    while not done() and time.monotonic() < deadline:
        if app is not None:
            app.processEvents()
        time.sleep(0.005)
    return done()


# ---- the loops that stop between items ------------------------------------------------------------------------------


class CancelLoopsTest(TestCase):
    """Each function that walks items takes ``should_cancel`` and ends at the next item once it answers True."""

    def test_a_scan_ends_between_folders_and_keeps_what_it_did(self):
        done: list = []

        def onboard(path):
            done.append(path)
            return {"status": "ok", "game_id": str(path)}

        with (
            mock.patch.object(scanning, "_discover_roots", return_value=iter(roots(5))),
            mock.patch.object(scanning, "_rom_folders", return_value={}),
            mock.patch.object(scanning, "_launcher_rule", return_value=lambda folder: False),
            mock.patch.object(scanning.onboarding, "onboard", side_effect=onboard),
            mock.patch.object(scanning.library, "enabled_plugins", return_value=[]),
            mock.patch.object(scanning.engine, "enabled_plugins", return_value=[]),
        ):
            results_ = scanning.scan_library("/g/games", should_cancel=lambda: len(done) >= 2)
        self.assertEqual((len(results_), len(done)), (2, 2))

    def test_a_look_ends_between_folders_and_writes_nothing(self):
        seen: list = []

        def preview(path):
            seen.append(path)
            return {"status": "error", "message": "not a game"}

        with (
            mock.patch.object(scanning, "_discover_roots", return_value=iter(roots(4))),
            mock.patch.object(scanning, "_rom_folders", return_value={}),
            mock.patch.object(scanning, "_launcher_rule", return_value=lambda folder: False),
            mock.patch.object(look.onboarding, "preview", side_effect=preview),
            mock.patch.object(look.library, "enabled_plugins", return_value=[]),
            mock.patch.object(look.engine, "enabled_plugins", return_value=[]),
        ):
            answer = look.look("/g/games", should_cancel=lambda: len(seen) >= 1)
        self.assertEqual(answer["status"], "cancelled")
        self.assertEqual(len(seen), 1)

    def test_adding_a_reviewed_look_ends_between_games_and_says_so(self):
        done: list = []

        def onboard(path, decision=None):
            done.append(path)
            return {"status": "ok", "game_id": f"id{len(done)}"}

        rows = [{"name": f"G{i}", "keep": True, "folders": [{"path": f"/g/games/{i}"}]} for i in range(4)]
        with mock.patch.object(look.onboarding, "onboard", side_effect=onboard):
            answer = look.add(rows, should_cancel=lambda: len(done) >= 1)
            whole = look.add(rows)
        self.assertEqual((answer["cancelled"], len(done)), (True, 1 + 4))
        self.assertFalse(whole["cancelled"])

    def test_recording_store_games_ends_between_games(self):
        found = [{"folder": f"/g/store/{i}", "key": f"steam:{i}", "title": f"T{i}"} for i in range(3)]
        done: list = []

        def onboard(folder):
            done.append(folder)
            return {"status": "ok", "path": folder}

        with (
            mock.patch.object(store_installs, "find", return_value=found),
            mock.patch.object(store_installs, "_tracked_folders", return_value=set()),
            mock.patch.object(store_installs, "_keep_store_id"),
            mock.patch.object(store_installs.onboarding, "onboard", side_effect=onboard),
        ):
            counts = store_installs.record_new(should_cancel=lambda: len(done) >= 1)
        self.assertEqual((counts["recorded"], counts["cancelled"], counts["found"]), (1, True, 3))

    def test_a_sync_ends_between_games(self):
        class Plugin:
            id = "toy"

            @staticmethod
            def list_owned():
                return [{"name": f"G{i}"} for i in range(5)]

        folded: list = []

        def fold(detection, own):
            folded.append(detection)
            return {}, True

        with (
            mock.patch.object(onboarding, "_own_entries", return_value=[]),
            mock.patch.object(onboarding, "record_ownership", side_effect=fold),
        ):
            answer = onboarding.sync_owned(Plugin, should_cancel=lambda: len(folded) >= 2)
        self.assertEqual((answer["created"], answer["cancelled"]), (2, True))

    def test_an_update_check_ends_between_threads_and_keeps_what_it_checked(self):
        me = "00000000-0000-4000-8000-0000000000aa"
        entries = [
            {
                "system_uuid": me,
                "game_id": f"g{i}",
                "canonical_name": f"Game {i}",
                "platform_ids": [{"platform": "f95", "id": 100 + i}],
                "versions": [{"version": "1.0", "variants": [{"id": f"v{i}", "path": f"/g/games/{i}"}]}],
            }
            for i in range(3)
        ]
        checked: list = []

        def full(thread_id, last_changed):
            checked.append(thread_id)
            return {"version": "2.0"}

        with (
            mock.patch("pythia.identity.get_system_uuid", return_value=me),
            mock.patch("pythia.datadir.list_all_entries", return_value=entries),
            mock.patch("pythia.database.games.save_entry_and_sync_cache"),
            mock.patch.object(f95_updates, "fast_check", return_value={100: 5, 101: 5, 102: 5}),
            mock.patch.object(f95_updates, "full_check", side_effect=full),
        ):
            answer = f95_updates.run_update_check(should_cancel=lambda: len(checked) >= 1)
        self.assertEqual((answer["checked"], answer["cancelled"]), (1, True))


# ---- games running that Pythia did not start -----------------------------------------------------------------------


def game(game_id, name, folder, system, steam=None):
    ids = [{"platform": "steam", "id": steam}] if steam else []
    return {
        "game_id": game_id,
        "entries": [record(system, game_id, name, [variant("v", folder)], platform_ids=ids)],
    }


class RunningExternalTest(TestCase):
    me = "00000000-0000-4000-8000-0000000000aa"

    def test_steams_registry_says_which_apps_are_running(self):
        registry = vdf_parse(io.StringIO(REGISTRY), {})
        self.assertEqual(running_external.running_apps_from_registry(registry), {"620", "440"})
        self.assertEqual(running_external.running_apps_from_registry({}), set())

    def test_a_steam_game_is_steams_and_is_not_quittable(self):
        library = [game("s1", "Portal 2", "/home/u/Steam/common/Portal2", self.me, steam="620")]
        found = running_external.detect(library, self.me, {"620"}, [])
        self.assertEqual(found, {"s1": {"via": "steam", "pid": None, "pids": [], "quittable": False}})
        self.assertEqual(running_external.detect(library, self.me, set(), []), {})

    def test_a_steam_game_is_never_taken_from_its_folder(self):
        library = [game("s1", "Portal 2", "/home/u/Steam/common/Portal2", self.me, steam="620")]
        processes = [(10, "/home/u/Steam/common/Portal2/portal2_linux", [])]
        self.assertEqual(running_external.detect(library, self.me, set(), processes), {})

    def test_a_game_is_found_by_the_program_in_its_folder_not_by_a_file_that_names_it(self):
        library = [game("g1", "Moonlit", "/home/u/Games/Moonlit", self.me)]
        processes = [
            (10, "/home/u/Games/Moonlit/bin/moonlit", ["moonlit"]),
            (11, "/usr/bin/vim", ["vim", "/home/u/Games/Moonlit/notes.txt"]),
            (12, "/usr/bin/wine", ["wine", "/home/u/Games/Moonlit/moonlit.exe"]),
            (13, "/home/u/Games/Moonlit2/other", []),
        ]
        found = running_external.detect(library, self.me, set(), processes)
        self.assertEqual(found["g1"], {"via": "process", "pid": 10, "pids": [10, 12], "quittable": True})

    def test_a_game_only_another_computer_has_and_a_too_short_folder_are_left_alone(self):
        other = "00000000-0000-4000-8000-0000000000bb"
        library = [game("g1", "Theirs", "/home/u/Games/Theirs", other), game("g2", "Short", "/usr", self.me)]
        processes = [(10, "/home/u/Games/Theirs/x", []), (11, "/usr/bin/anything", [])]
        self.assertEqual(running_external.detect(library, self.me, set(), processes), {})

    def test_a_game_pythia_started_is_skipped(self):
        library = [game("g1", "Moonlit", "/home/u/Games/Moonlit", self.me)]
        processes = [(10, "/home/u/Games/Moonlit/bin/moonlit", [])]
        self.assertEqual(running_external.detect(library, self.me, set(), processes, skip=lambda g: g == "g1"), {})


class TrackerExternalTest(TestCase):
    def setUp(self):
        self.tracker = running.Tracker()
        self.told: list = []
        self.tracker.subscribe(lambda kind, info: self.told.append((kind, info)))

    def test_a_game_that_begins_and_ends_is_told_like_one_pythia_started(self):
        found = {"g1": {"via": "process", "pid": 5, "pids": [5], "quittable": True}}
        self.tracker.set_external(found)
        self.tracker.set_external(found)
        self.assertEqual([k for k, _ in self.told], ["started"])
        self.assertEqual(self.told[0][1]["started_at"], None)
        self.assertEqual([g["game_id"] for g in self.tracker.games()], ["g1"])
        self.assertTrue(self.tracker.is_running("g1"))
        self.assertFalse(self.tracker.is_tracked("g1"))
        self.tracker.set_external({})
        self.assertEqual([k for k, _ in self.told], ["started", "stopped"])
        self.assertEqual(self.told[1][1]["exit_code"], None)
        self.assertEqual(self.tracker.games(), [])

    def test_a_game_found_by_its_folder_is_ended_through_the_system_and_steams_is_not(self):
        self.tracker.set_external(
            {
                "g1": {"via": "process", "pid": 5, "pids": [5, 6], "quittable": True},
                "s1": {"via": "steam", "pid": None, "pids": [], "quittable": False},
            }
        )
        with mock.patch.object(running, "_terminate_pid") as terminate:
            self.assertTrue(self.tracker.quit("g1"))
            self.assertFalse(self.tracker.quit("s1"))
            self.assertFalse(self.tracker.quit("nope"))
        self.assertEqual([c.args[0] for c in terminate.call_args_list], [5, 6])
        self.assertEqual(self.tracker.why_not("s1"), running.STEAM_OWNS)
        self.assertEqual(self.tracker.why_not("nope"), running.NOT_STARTED_HERE)

    def test_the_observer_reads_and_tells_the_tracker(self):
        me = "00000000-0000-4000-8000-0000000000aa"
        library = [game("g1", "Moonlit", "/home/u/Games/Moonlit", me)]
        live = [(10, "/home/u/Games/Moonlit/bin/moonlit", [])]
        observer = running_external.Observer(
            lambda: library, me, self.tracker, steam=lambda: set(), process_list=lambda: list(live)
        )
        observer.poll()
        self.assertEqual([g["pid"] for g in self.tracker.games()], [10])
        live.clear()
        observer.poll()
        self.assertEqual(self.tracker.games(), [])

    def test_a_reading_that_fails_is_no_change_not_a_dead_observer(self):
        def broken():
            raise OSError("no /proc")

        observer = running_external.Observer(lambda: [], "me", self.tracker, steam=set, process_list=broken)
        observer.poll()
        self.assertEqual(self.tracker.games(), [])


# ---- art for every role ---------------------------------------------------------------------------------------------


class ArtRolesTest(LibraryCase):
    def setUp(self):
        super().setUp()
        self.root = self.art / "Steam"
        for p in (mock.patch("pythia.util.steam.config.get_steam_dir", lambda: str(self.root)),):
            p.start()
            self.addCleanup(p.stop)
        self.save(
            record(
                self.me,
                "g1",
                "Portal 2",
                [variant("a", "/g/portal")],
                platform_ids=[{"platform": "steam", "id": "620"}],
            )
        )
        self.entries = self.game("g1")["entries"]

    def shots(self, *names):
        folder = self.root / "userdata" / "1234" / "760" / "remote" / "620" / "screenshots"
        (folder / "thumbnails").mkdir(parents=True, exist_ok=True)
        (folder / "thumbnails" / names[0]).write_bytes(b"thumb")
        for age, name in enumerate(names):
            path = folder / name
            path.write_bytes(name.encode())
            stamp = 1_700_000_000 + age * 100
            os.utime(path, (stamp, stamp))
        return folder

    def test_steams_newer_layout_keeps_each_file_in_a_hash_folder(self):
        folder = self.steam / "620" / "0123abcd"
        folder.mkdir(parents=True)
        for name in ("library_hero.jpg", "library_header.jpg", "logo.png"):
            (folder / name).write_bytes(name.encode())
        for role, expected in (
            ("background", "library_hero.jpg"),
            ("banner", "library_header.jpg"),
            ("logo", "logo.png"),
        ):
            path, reason = gaming_api.art_path(self.entries, role)
            self.assertEqual((path.name if path else None, reason), (expected, None), role)

    def test_screenshots_are_steams_newest_first_and_the_thumbnails_are_not_among_them(self):
        self.shots("old.jpg", "new.png")
        listed = gaming_api.art_list(self.entries)
        names = [(s["index"], Path(s["path"]).name) for s in listed["screenshots"]]
        self.assertEqual(names, [(0, "new.png"), (1, "old.jpg")])
        self.assertEqual(listed["screenshots"][0]["mime"], "image/png")
        path, _reason = gaming_api.art_path(self.entries, "screenshot")
        self.assertEqual(path.name, "new.png")
        path, _reason = gaming_api.art_path(self.entries, "screenshot:1")
        self.assertEqual(path.name, "old.jpg")
        path, reason = gaming_api.art_path(self.entries, "screenshot:5")
        self.assertIsNone(path)
        self.assertIn("only 2", reason)

    def test_without_screenshots_the_reason_says_where_they_would_be(self):
        path, reason = gaming_api.art_path(self.entries, "screenshot")
        self.assertIsNone(path)
        self.assertIn("screenshots", reason)
        self.assertEqual(gaming_api.art_list(self.entries)["screenshots"], [])

    def test_the_list_has_every_role_with_its_file_or_its_reason(self):
        (self.coverart / "portal-2.jpg").write_bytes(b"cover")
        listed = gaming_api.art_list(self.entries)
        self.assertEqual(sorted(listed["roles"]), ["background", "banner", "cover", "icon", "logo"])
        self.assertEqual(Path(listed["roles"]["cover"]["path"]).name, "portal-2.jpg")
        self.assertIsNone(listed["roles"]["logo"])
        self.assertIn("logo", listed["missing"]["logo"])
        self.assertEqual(sorted(listed["missing"]), ["background", "banner", "icon", "logo"])

    def test_a_screenshot_can_be_read_as_bytes(self):
        self.shots("only.jpg")
        answer = gaming_api.art_bytes(self.entries, "screenshot:0", 0, 100)
        self.assertEqual((answer["status"], answer["eof"], answer["size"]), ("ok", True, len(b"only.jpg")))


# ---- the Backend ----------------------------------------------------------------------------------------------------


@skipUnless(HAVE_QT, "PySide6 is not installed")
class BatchThreeSlotsTest(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = tempfile.TemporaryDirectory(prefix="pythia-gaming3-", ignore_cleanup_errors=True)
        cls.datadir = os.path.join(cls.data.name, "dd")
        cls.env = mock.patch.dict(
            os.environ, {"PYTHIA_DATA_DIR": cls.data.name, "PYTHIA_DATADIR_OVERRIDE": cls.datadir}
        )
        cls.env.start()
        cls.app = QGuiApplication.instance() or QGuiApplication([])
        from pythia.ui.backend import Backend

        cls.backend = Backend()
        running_external.stop()

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
        self.addCleanup(self.backend._jobs.clear)
        self.addCleanup(running.TRACKER.set_external, {})

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
        self.put("g1", "Moonlit Harbor", [{"id": "a", "path": "/g/games/Moonlit", "platform": "Linux", "year": 1999}])
        self.put(
            "g2",
            "Portal 2",
            [{"id": "b", "path": "/g/steam/Portal2", "platform": "Linux"}],
            platform_ids=[{"platform": "steam", "id": "620"}],
        )
        self.backend.refresh_games()

    def finished(self, job_id):
        return wait_for(self.app, lambda: self.job(job_id)["state"] != "running")

    def job(self, job_id):
        return next(j for j in self.backend._jobs if j["id"] == job_id)

    # ---- cancel ----
    def test_a_cancellable_job_is_stopped_by_setting_its_flag_and_ends_cancelled(self):
        job = self.backend._job_start("scan", "A scan", cancellable=True)
        self.assertTrue(self.job(job)["cancellable"])
        self.assertFalse(self.backend._cancel_flag(job)())
        answer = self.backend.cancel_job(job)
        self.assertEqual((answer["status"], answer["stopping"]), ("ok", True))
        self.assertTrue(self.backend._cancel_flag(job)())
        self.backend._job_finish(job, "Stopped: 1 game found", cancelled=True)
        self.assertEqual((self.job(job)["state"], self.job(job)["cancelled"]), ("done", True))
        self.assertEqual(self.backend.cancel_job(job)["status"], "unavailable")

    def test_a_job_that_cannot_stop_between_items_says_so(self):
        job = self.backend._job_start("wine", "Set up Windows games")
        answer = self.backend.cancel_job(job)
        self.assertEqual(answer["status"], "unavailable")
        self.assertIn("wine", answer["message"])
        self.assertFalse(self.job(job)["cancellable"])

    def test_a_scan_started_for_real_stops_between_folders(self):
        done: list = []
        backend = self.backend

        def onboard(path):
            done.append(path)
            if len(done) == 2:
                backend.cancel_job(backend._jobs[0]["id"])
            return {"status": "ok", "game_id": str(path), "candidates": {"suggested": None}}

        with (
            mock.patch.object(scanning, "_discover_roots", return_value=iter(roots(6))),
            mock.patch.object(scanning, "_rom_folders", return_value={}),
            mock.patch.object(scanning, "_launcher_rule", return_value=lambda folder: False),
            mock.patch.object(scanning.onboarding, "onboard", side_effect=onboard),
            mock.patch.object(scanning.library, "enabled_plugins", return_value=[]),
            mock.patch.object(scanning.engine, "enabled_plugins", return_value=[]),
        ):
            backend.start_scan("/g/games")
            job = backend._jobs[0]["id"]
            self.assertTrue(self.finished(job))
        self.assertEqual(len(done), 2)
        self.assertTrue(self.job(job)["cancelled"])
        self.assertTrue(self.job(job)["summary"].startswith("Stopped: "))

    def test_combining_records_stops_between_records(self):
        calls: list = []
        backend = self.backend

        def merge(surviving, retired):
            calls.append(retired)
            if len(calls) == 1:
                backend.cancel_job(backend._jobs[0]["id"])
            return {"status": "ok"}

        with mock.patch.object(reconciliation, "merge", side_effect=merge):
            backend.merge_cluster("s", ["a", "b", "c"])
            job = backend._jobs[0]["id"]
            self.assertTrue(self.finished(job))
        self.assertEqual(calls, ["a"])
        self.assertTrue(self.job(job)["cancelled"])
        self.assertEqual(self.job(job)["summary"], "Stopped: Combined 2 records into one")

    # ---- running ----
    def observer(self, steam, processes):
        return running_external.Observer(
            self.backend._library_records, self.me, running.TRACKER, steam=lambda: steam, process_list=lambda: processes
        )

    def test_steams_game_and_a_game_already_running_are_listed_and_told(self):
        self.library()
        started: list = []
        stopped: list = []
        self.backend.gameStarted.connect(started.append)
        self.backend.gameStopped.connect(stopped.append)
        observer = self.observer({"620"}, [(77, "/g/games/Moonlit/bin/moonlit", [])])
        observer.poll()
        listed = self.backend.get_running_games()
        by_id = {g["game_id"]: g for g in listed["games"]}
        self.assertEqual(sorted(by_id), ["g1", "g2"])
        self.assertEqual((by_id["g2"]["via"], by_id["g2"]["quittable"], by_id["g2"]["pid"]), ("steam", False, None))
        self.assertEqual((by_id["g1"]["via"], by_id["g1"]["quittable"], by_id["g1"]["pid"]), ("process", True, 77))
        self.assertEqual(sorted(s["game_id"] for s in started), ["g1", "g2"])
        self.assertEqual(self.check("get_running_games", listed), [])
        self.assertEqual(self.check("gameStarted", started[0], event=True), [])
        self.observer(set(), []).poll()
        self.assertEqual(self.backend.get_running_games()["games"], [])
        self.assertEqual(sorted(s["game_id"] for s in stopped), ["g1", "g2"])
        self.assertEqual(self.check("gameStopped", stopped[0], event=True), [])

    def test_quitting_steams_game_says_steam_ends_it_and_a_found_game_is_ended_through_the_system(self):
        self.library()
        self.observer({"620"}, [(77, "/g/games/Moonlit/bin/moonlit", [])]).poll()
        steam = self.backend.quit_game("g2")
        self.assertEqual((steam["status"], steam["message"]), ("unavailable", running.STEAM_OWNS))
        with mock.patch.object(running, "_terminate_pid") as terminate:
            asked = self.backend.quit_game("g1")
        self.assertEqual((asked["status"], asked["asked"]), ("ok", True))
        terminate.assert_called_once_with(77)

    # ---- art ----
    def test_the_art_list_and_a_screenshot_come_through_the_backend(self):
        from pythia import covers

        self.library()
        art = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, art, True)
        steam = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, steam, True)
        shots = steam / "userdata" / "1" / "760" / "remote" / "620" / "screenshots"
        shots.mkdir(parents=True)
        (shots / "one.jpg").write_bytes(b"shot")
        settled: list = []
        self.backend.asyncResult.connect(settled.append)

        def later(pending):
            self.assertTrue(wait_for(self.app, lambda: any(s["request"] == pending["request"] for s in settled)))
            return next(s for s in settled if s["request"] == pending["request"])

        with (
            mock.patch.object(covers.settings, "COVERART_PATH", str(art)),
            mock.patch("pythia.util.steam.config.get_steam_dir", lambda: str(steam)),
        ):
            listed = later(self.backend.get_game_art_list("g2"))
            self.assertEqual(listed["status"], "ok")
            self.assertEqual([s["index"] for s in listed["screenshots"]], [0])
            self.assertEqual(self.check("get_game_art_list", listed), [])
            shot = later(self.backend.get_game_art("g2", "screenshot:0"))
            self.assertEqual((shot["status"], shot["mime"]), ("ok", "image/jpeg"))
            self.assertEqual(self.check("get_game_art", shot), [])
            data = later(self.backend.get_game_art_data("g2", "screenshot", 0, 100))
            self.assertEqual(self.check("get_game_art_data", data), [])
            missing = later(self.backend.get_game_art_list("nope"))
            self.assertEqual(missing["code"], "no-such-game")

    # ---- typed results ----
    def check(self, name, answer, event=False):
        """The problems of ``answer`` against the type its slot (or event) declared."""
        if event:
            declared = results.EVENT_PAYLOADS[name]
            return results.problems(answer, declared)
        from pythia.ui.backend import Backend

        declared = results.result_type_of(getattr(Backend, name))
        self.assertIsNotNone(declared, f"{name} declares no result type")
        self.assertEqual(answer["status"], "ok", answer)
        return results.answer_problems(answer, declared)

    def test_what_the_slots_answer_is_what_their_types_say(self):
        self.library()
        self.put("g3", "Played", [{"id": "c", "path": "/g/games/Played", "platform": "Windows", "playcount": 2}])
        self.backend.refresh_games()
        games = self.backend.get_games()
        self.assertEqual(self.check("get_games", games), [])
        self.assertEqual(self.check("get_games", self.backend.get_games(games["version"])), [])
        self.assertEqual(self.check("get_game_systems", self.backend.get_game_systems()), [])
        self.assertEqual(self.check("get_game_detail", self.backend.get_game_detail("g3")), [])
        queried = self.backend.query_games({"text": "o", "limit": 2})
        self.assertEqual(self.check("query_games", queried), [])
        self.assertEqual(self.check("query_games", self.backend.query_games({"facets": False})), [])
        made = self.backend.create_collection("Co-op")
        self.assertEqual(self.check("create_collection", made), [])
        cid = made["collection"]["id"]
        self.assertEqual(self.check("get_collections", self.backend.get_collections()), [])
        self.assertEqual(self.check("add_to_collection", self.backend.add_to_collection(cid, ["g1", "nope"])), [])
        self.assertEqual(self.check("remove_from_collection", self.backend.remove_from_collection(cid, ["g1"])), [])
        self.assertEqual(self.check("rename_collection", self.backend.rename_collection(cid, "Couch")), [])
        self.assertEqual(self.check("delete_collection", self.backend.delete_collection(cid)), [])
        job = self.backend._job_start("scan", "A scan", cancellable=True)
        self.assertEqual(self.check("cancel_job", self.backend.cancel_job(job)), [])
        self.assertEqual(self.check("quit_game", {"status": "ok", "game_id": "g1", "asked": True}), [])

    def test_a_field_the_type_does_not_name_or_a_wrong_type_is_a_problem(self):
        from pythia.ui.backend import Backend

        declared = results.result_type_of(Backend.cancel_job)
        self.assertEqual(results.problems({"job": "x", "stopping": True}, declared), [])
        self.assertTrue(results.problems({"job": "x"}, declared))
        self.assertTrue(results.problems({"job": "x", "stopping": "yes"}, declared))
        self.assertTrue(results.problems({"job": "x", "stopping": True, "extra": 1}, declared))

    def test_the_schema_has_the_type_of_each_declared_slot_and_event(self):
        from pythia.ipc import schema

        described = schema.backend_schema()
        by_name = {m["name"]: m for m in described["methods"]}
        self.assertEqual(by_name["query_games"]["result"], "QueryGamesResult")
        self.assertEqual(by_name["get_game_art_list"]["result"], "GetGameArtListResult")
        self.assertNotIn("result", by_name["get_modes"])
        types = described["types"]
        self.assertEqual(types["CancelJobResult"]["fields"]["stopping"], {"type": "boolean"})
        self.assertEqual(types["Card"]["fields"]["last_played"], {"type": {"oneOf": ["integer", "null"]}})
        self.assertEqual(types["GetGamesResult"]["fields"]["games"], {"type": {"array": {"object": "Card"}}})
        facets = types["QueryGamesResult"]["fields"]["facets"]
        self.assertEqual(facets, {"type": {"object": "Facets"}, "optional": True})
        status = described["envelope"]["fields"]["status"]["type"]
        self.assertEqual(status, {"enum": ["ok", "unavailable", "error", "pending"]})
        events = {e["name"]: e for e in described["events"]}
        self.assertEqual(events["collectionsChanged"]["payload"], "CollectionsChanged")
