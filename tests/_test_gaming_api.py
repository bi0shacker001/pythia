"""What a gaming front end reads of the library (pythia.gaming_api) and the games Pythia started (pythia.running),
over real records: datadir entries written and read back the way Pythia keeps them, not hand-made UI data."""

import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from unittest import TestCase, mock

from pythia import covers, datadir, favourites, gaming_api, running, settings
from pythia.database import schema
from pythia.identity import get_system_uuid
from pythia.util.test_config import setup_test_environment

setup_test_environment()

OTHER = "00000000-0000-4000-8000-0000000000bb"


def variant(vid, path=None, **fields):
    base = {"id": vid}
    if path:
        base["path"] = path
    base.update(fields)
    return base


def record(system, game_id, name, variants=(), version="1.0", **fields):
    entry = {
        "system_uuid": system,
        "game_id": game_id,
        "canonical_name": name,
        "platform_ids": fields.pop("platform_ids", []),
        "versions": [{"version": version, "variants": list(variants)}],
    }
    entry.update(fields)
    return entry


class LibraryCase(TestCase):
    """A datadir of its own, and the library read the way Backend.refresh_games reads it."""

    def setUp(self):
        folder = tempfile.mkdtemp()
        patch = mock.patch.dict(os.environ, {"PYTHIA_DATADIR_OVERRIDE": folder})
        patch.start()
        self.addCleanup(patch.stop)
        if os.path.exists(settings.DB_PATH):
            os.remove(settings.DB_PATH)
        schema.syncdb()
        self.me = get_system_uuid()
        self.art = Path(tempfile.mkdtemp())
        self.coverart = self.art / "coverart"
        self.banners = self.art / "banners"
        self.icons = self.art / "icons"
        self.steam = self.art / "librarycache"
        for d in (self.coverart, self.banners, self.icons, self.steam):
            d.mkdir()
        covers._steam_cache.cache_clear()
        for p in (
            mock.patch.object(covers.settings, "COVERART_PATH", str(self.coverart)),
            mock.patch.object(covers.settings, "BANNER_PATH", str(self.banners)),
            mock.patch.object(covers.settings, "ICON_PATH", str(self.icons)),
            mock.patch.object(covers, "_steam_cache", lambda: self.steam),
        ):
            p.start()
            self.addCleanup(p.stop)

    def save(self, entry):
        datadir.save_entry(entry)

    def library(self):
        """The records of Backend._games, from what is on disk."""
        return [
            {
                "game_id": gid,
                "entries": entries,
                "cover": covers.cover_url(entries),
                "favourite": favourites.is_favourite(entries),
            }
            for gid, entries in sorted(datadir.list_known_games().items())
        ]

    def game(self, game_id):
        return next(g for g in self.library() if g["game_id"] == game_id)


class CardsTest(LibraryCase):
    def test_a_card_has_what_a_list_row_reads(self):
        self.save(
            record(
                self.me,
                "g1",
                "Moonlit Harbor",
                [
                    variant(
                        "v1",
                        "/games/Moonlit",
                        platforms=["Windows"],
                        runner="wine",
                        lastplayed=1_700_000_000,
                        playtime=1.5,
                        detected_at="2026-10-01T10:00:00+00:00",
                        year=2019,
                        source={"kind": "folder"},
                    ),
                    variant("v2", "/games/Moonlit2", platform="Linux", playtime=0.25, lastplayed=1_700_005_000),
                ],
                sortname="Harbor, Moonlit",
                platform_ids=[{"platform": "steam", "id": "620"}],
            )
        )
        (self.coverart / "moonlit-harbor.jpg").write_bytes(b"jpg")
        card = gaming_api.card(self.game("g1"), self.me)
        self.assertEqual(
            (card["id"], card["title"], card["sort_name"]), ("g1", "Moonlit Harbor", "Harbor, Moonlit")
        )
        self.assertEqual(card["platforms"], ["Windows", "Linux"])
        self.assertEqual((card["runner"], card["runners"]), ("wine", ["wine"]))
        self.assertTrue(card["installed"])
        self.assertEqual((card["last_played"], card["playtime_hours"]), (1_700_005_000, 1.75))
        self.assertEqual((card["year"], card["added"]), (2019, "2026-10-01T10:00:00+00:00"))
        self.assertEqual((card["stores"], card["sources"]), (["steam"], ["folder"]))
        self.assertEqual(card["art"], ["cover"])
        self.assertFalse(card["favourite"])
        self.assertFalse(card["has_update"])

    def test_a_game_only_another_computer_has_is_not_installed_here_and_a_missing_cover_is_no_art(self):
        self.save(record(OTHER, "g2", "Celeste", [variant("v1", "/elsewhere/Celeste", platform="Linux")]))
        card = gaming_api.card(self.game("g2"), self.me)
        self.assertEqual((card["title"], card["installed"], card["art"]), ("Celeste", False, []))
        self.assertEqual(card["sort_name"], "celeste")

    def test_played_at_reads_whole_seconds_and_a_utc_text_and_ignores_what_it_cannot_read(self):
        self.save(
            record(
                self.me,
                "g3",
                "Times",
                [
                    variant("a", "/g/a", lastplayed="2026-10-20T10:00:00+00:00"),
                    variant("b", "/g/b", lastplayed="1700000000"),
                    variant("c", "/g/c", lastplayed="never"),
                    variant("d", "/g/d", lastplayed=0),
                ],
            )
        )
        card = gaming_api.card(self.game("g3"), self.me)
        self.assertEqual(card["last_played"], 1_792_490_400)

    def test_a_favourite_and_an_update_show_on_the_card(self):
        self.save(record(self.me, "g4", "Hades", [variant("v", "/g/h")], available_updates=[{"version": "2"}]))
        favourites.set_favourite("g4", True, tell_handhelds=False)
        card = gaming_api.card(self.game("g4"), self.me)
        self.assertTrue(card["favourite"])
        self.assertTrue(card["has_update"])

    def test_making_the_cards_reads_no_file_and_asks_no_art_lookup(self):
        self.save(record(self.me, "g5", "Quiet", [variant("v", "/g/q", platform="Linux")]))
        library = self.library()
        with (
            mock.patch.object(covers, "cover_for", side_effect=AssertionError("a lookup per game")),
            mock.patch.object(datadir, "list_known_games", side_effect=AssertionError("a read per list")),
            mock.patch("builtins.open", side_effect=AssertionError("a file read")),
        ):
            built = gaming_api.cards(library, self.me)
        self.assertEqual([c["id"] for c in built], ["g5"])

    def test_the_version_follows_the_cards(self):
        self.save(record(self.me, "g6", "Stable", [variant("v", "/g/s")]))
        first = gaming_api.version(gaming_api.cards(self.library(), self.me))
        again = gaming_api.version(gaming_api.cards(self.library(), self.me))
        favourites.set_favourite("g6", True, tell_handhelds=False)
        changed = gaming_api.version(gaming_api.cards(self.library(), self.me))
        self.assertEqual(first, again)
        self.assertNotEqual(first, changed)


class SystemsTest(LibraryCase):
    def test_games_are_counted_under_each_platform_they_have_in_one_pass(self):
        self.save(record(self.me, "a", "Alpha", [variant("1", "/g/a", platform="Linux", lastplayed=100)]))
        self.save(
            record(
                self.me,
                "b",
                "Beta",
                [variant("2", "/g/b1", platform="Linux", lastplayed=300), variant("3", "/g/b2", platform="Windows")],
            )
        )
        self.save(record(OTHER, "c", "Gamma", []))
        favourites.set_favourite("b", True, tell_handhelds=False)
        systems = gaming_api.systems(gaming_api.cards(self.library(), self.me))
        by_name = {s["name"]: s for s in systems}
        self.assertEqual([s["name"] for s in systems], ["Linux", "Windows", "Unknown platform"])
        self.assertEqual((by_name["Linux"]["id"], by_name["Linux"]["count"]), ("linux", 2))
        self.assertEqual((by_name["Linux"]["favourites"], by_name["Linux"]["last_played"]), (1, 300))
        self.assertEqual((by_name["Windows"]["count"], by_name["Windows"]["installed"]), (1, 1))
        self.assertEqual((by_name["Unknown platform"]["id"], by_name["Unknown platform"]["count"]), ("unknown", 1))
        self.assertEqual(by_name["Unknown platform"]["installed"], 0)


class DetailTest(LibraryCase):
    def test_a_detail_gives_what_pythia_records_and_names_what_it_does_not(self):
        self.save(
            record(
                self.me,
                "g1",
                "Ironvale",
                [variant("v", "/g/i", platform="Linux", year=2021)],
                notes="Started chapter 2",
                ownership=[{"platform": "steam"}],
                platform_ids=[{"platform": "steam", "id": "440"}],
                current_version="1.0",
            )
        )
        answer = gaming_api.detail(self.game("g1"), self.me)
        fields = answer["fields"]
        self.assertEqual((fields["title"], fields["year"], fields["notes"]), ("Ironvale", 2021, "Started chapter 2"))
        self.assertEqual(fields["stores"], [{"platform": "steam", "id": "440"}])
        self.assertEqual((fields["ownership"], fields["current_version"]), (["steam"], "1.0"))
        self.assertEqual(fields["versions"], [{"version": "1.0", "variants": 1}])
        self.assertEqual(sorted(answer["missing"]), ["description", "developer", "genre", "publisher", "rating"])
        for entry in answer["missing"].values():
            self.assertEqual(entry["status"], "unavailable")
            self.assertTrue(entry["message"])
        for name in answer["missing"]:
            self.assertNotIn(name, fields)

    def test_a_field_a_record_does_carry_is_given_and_not_listed_as_missing(self):
        self.save(record(self.me, "g2", "Carried", [variant("v", "/g/c")], developer="Studio X"))
        answer = gaming_api.detail(self.game("g2"), self.me)
        self.assertEqual(answer["fields"]["developer"], "Studio X")
        self.assertNotIn("developer", answer["missing"])


class ArtTest(LibraryCase):
    def entries(self, name="Moonlit Harbor", steam=None):
        ids = [{"platform": "steam", "id": steam}] if steam else []
        self.save(record(self.me, "g1", name, [variant("v", "/g/m")], platform_ids=ids))
        return self.game("g1")["entries"]

    def test_each_role_comes_from_the_art_already_on_this_computer(self):
        (self.coverart / "moonlit-harbor.jpg").write_bytes(b"c")
        (self.banners / "moonlit-harbor.jpg").write_bytes(b"banner")
        (self.icons / "pythia_moonlit-harbor.png").write_bytes(b"i")
        (self.steam / "620_logo.png").write_bytes(b"l")
        (self.steam / "620").mkdir()
        (self.steam / "620" / "library_hero.jpg").write_bytes(b"h")
        entries = self.entries(steam="620")
        roles = ("cover", "banner", "icon", "logo", "background")
        found = {role: gaming_api.art_path(entries, role)[0] for role in roles}
        self.assertEqual(found["cover"], self.coverart / "moonlit-harbor.jpg")
        self.assertEqual(found["banner"], self.banners / "moonlit-harbor.jpg")
        self.assertEqual(found["icon"], self.icons / "pythia_moonlit-harbor.png")
        self.assertEqual(found["logo"], self.steam / "620_logo.png")
        self.assertEqual(found["background"], self.steam / "620" / "library_hero.jpg")

    def test_the_answer_names_the_file_and_its_type_and_size(self):
        (self.banners / "moonlit-harbor.jpg").write_bytes(b"banner")
        answer = gaming_api.art(self.entries(), "banner")
        self.assertEqual(
            (answer["status"], answer["role"], answer["mime"], answer["size"]), ("ok", "banner", "image/jpeg", 6)
        )
        self.assertEqual(Path(answer["path"]), self.banners / "moonlit-harbor.jpg")
        self.assertEqual(answer["uri"], (self.banners / "moonlit-harbor.jpg").as_uri())

    def test_no_file_is_unavailable_with_the_reason_and_nothing_is_made_up(self):
        entries = self.entries(steam="999")
        for role in ("cover", "banner", "icon", "logo", "background"):
            answer = gaming_api.art(entries, role)
            self.assertEqual(answer["status"], "unavailable", role)
            self.assertIn(role, answer["message"])
        self.assertEqual(gaming_api.art(entries, "screenshot")["status"], "unavailable")
        self.assertIn("screenshots", gaming_api.art(entries, "screenshot")["message"])
        self.assertIn("no art role", gaming_api.art(entries, "poster")["message"])


def sleeper():
    kwargs = {} if os.name == "nt" else {"start_new_session": True}
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], **kwargs)  # noqa: S603


class RunningTest(TestCase):
    def setUp(self):
        self.tracker = running.Tracker()
        self.events = []
        self.stopped = threading.Event()
        self.tracker.subscribe(self.heard)
        self.procs = []

    def tearDown(self):
        for proc in self.procs:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=20)

    def heard(self, kind, info):
        self.events.append((kind, info))
        if kind == "stopped":
            self.stopped.set()

    def start(self, game_id="g1"):
        proc = sleeper()
        self.procs.append(proc)
        self.tracker.track(game_id, proc, "folder")
        return proc

    def test_a_started_game_is_running_until_it_ends_and_both_are_told(self):
        proc = self.start()
        self.assertTrue(self.tracker.is_running("g1"))
        games = self.tracker.games()
        self.assertEqual([(g["game_id"], g["via"], g["pid"]) for g in games], [("g1", "folder", proc.pid)])
        self.assertEqual(self.events[0][0], "started")
        self.assertEqual(self.events[0][1]["game_id"], "g1")
        proc.kill()
        self.assertTrue(self.stopped.wait(20), "the end was not noticed")
        kinds = [k for k, _ in self.events]
        self.assertEqual(kinds, ["started", "stopped"])
        self.assertFalse(self.tracker.is_running("g1"))
        self.assertEqual(self.tracker.games(), [])
        self.assertIn("exit_code", self.events[1][1])

    def test_quit_ends_a_tracked_game_and_the_stop_is_told(self):
        proc = self.start()
        self.assertTrue(self.tracker.quit("g1"))
        self.assertTrue(self.stopped.wait(30), "the game did not end")
        self.assertIsNotNone(proc.poll())
        self.assertEqual([k for k, _ in self.events], ["started", "stopped"])

    def test_a_game_this_tracker_does_not_have_cannot_be_ended(self):
        self.assertFalse(self.tracker.quit("never-started"))
        self.assertFalse(self.tracker.is_running("never-started"))
