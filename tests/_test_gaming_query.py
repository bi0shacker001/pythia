"""The platform names as droidtop's systems (pythia.platform_systems) and the query over the library's cards
(pythia.gaming_query), over datadir records written and read back the way Pythia keeps them."""

from pathlib import Path
from unittest import TestCase, mock, skipUnless

from pythia import gaming_api, gaming_query, platform_systems
from pythia.util.test_config import setup_test_environment
from tests._test_gaming_api import LibraryCase, record, variant

setup_test_environment()

SUBSET = Path(__file__).resolve().parent / "fixtures" / "platforms-database-subset.json"


# What Lutris, Pythia's runners and libretro's map write as a game's platform, and the droidtop-platforms system each is.
COMMON_PLATFORMS = {
    "Nintendo Entertainment System": "nes",
    "Nintendo NES": "nes",
    "Super Nintendo Entertainment System": "snes",
    "Nintendo SNES": "snes",
    "Sony PlayStation": "psx",
    "Sony PlayStation 2": "ps2",
    "Sega Genesis": "genesis",
    "Sega Genesis/Mega Drive": "genesis",
    "Windows": "windows",
    "Linux": "pc",
    "MS-DOS": "dos",
    "MSX, MSX2, MSX2+, MSX turboR": "msx",
    "Microsoft MSX": "msx",
    "Atari 8bit computers": "atari800",
    "Apple IIe": "apple2",
    "NEC PC Engine TurboGrafx-16": "pcengine",
    "Nintendo Game Boy (Color)": "gbc",
    "Wii U": "wiiu",
    "Magnavox Odyssey²": "odyssey2",
    "Phillips Videopac+": "videopac",
}


class SystemsTest(TestCase):
    def test_a_platform_name_is_a_droidtop_system_by_its_name_id_or_alias_ignoring_case_and_punctuation(self):
        ps2 = platform_systems.system_of("Sony PlayStation 2", SUBSET)
        self.assertEqual(ps2, {"id": "ps2", "name": "Sony PlayStation 2", "known": True})
        self.assertEqual(platform_systems.system_of("sony playstation-2", SUBSET)["id"], "ps2")
        self.assertEqual(platform_systems.system_of("NES", SUBSET)["id"], "nes")
        self.assertEqual(platform_systems.system_of("super nintendo entertainment system", SUBSET)["id"], "snes")

    def test_the_names_the_launchers_and_runners_write_are_their_systems(self):
        for name, ident in COMMON_PLATFORMS.items():
            with self.subTest(name):
                found = platform_systems.system_of(name, SUBSET)
                self.assertEqual((found["id"], found["known"]), (ident, True))

    def test_a_name_with_no_system_is_not_guessed_at(self):
        self.assertEqual(
            platform_systems.system_of("Strange Box 9000", SUBSET),
            {"id": "strange-box-9000", "name": "Strange Box 9000", "known": False},
        )

    def test_without_the_data_nothing_is_mapped(self):
        missing = Path(__file__).parent / "fixtures" / "no-such-database.json"
        self.assertFalse(platform_systems.system_of("Sony PlayStation 2", missing)["known"])
        self.assertEqual(len(platform_systems.load(missing)), 0)

    @skipUnless(platform_systems.PINNED.is_file(), "the droidtop-platforms submodule is not checked out")
    def test_the_pinned_droidtop_platforms_data_has_every_name_pythia_uses(self):
        systems = platform_systems.load()
        self.assertGreater(len(systems), 100)
        for name, ident in COMMON_PLATFORMS.items():
            with self.subTest(name):
                self.assertEqual(systems.system_of(name)["id"], ident)


class QueryCase(LibraryCase):
    """Six games over two computers' records, with platforms, runners, stores, years, installs and a favourite."""

    def setUp(self):
        super().setUp()
        # The systems of a small file in the shape of the pinned data, so these tests do not need the submodule.
        patch = mock.patch.object(platform_systems, "PINNED", SUBSET)
        patch.start()
        self.addCleanup(patch.stop)
        self.save(
            record(
                self.me,
                "g1",
                "Moonlit Harbor",
                [variant("a", "/g/moon", platform="Windows", runner="wine", year=1999, playcount=3, playtime=2.0)],
                platform_ids=[{"platform": "steam", "id": "620"}],
            )
        )
        self.save(
            record(
                self.me,
                "g2",
                "Moonlit Fields",
                [variant("b", "/g/fields", platform="Linux", runner="linux", year=2004, lastplayed=500)],
            )
        )
        self.save(
            record(
                self.me,
                "g3",
                "Crash Racer",
                [variant("c", "/g/crash", platform="Sony PlayStation", runner="duckstation", year=1996)],
            )
        )
        self.save(
            record(
                self.me,
                "g4",
                "Crash Racer 2",
                [variant("d", "/g/crash2", platform="Sony PlayStation 2", runner="pcsx2", year=2002)],
            )
        )
        self.save(
            record(
                "00000000-0000-4000-8000-0000000000bb",
                "g5",
                "Celeste",
                [variant("e", platform="Windows", year=2018)],
                platform_ids=[{"platform": "steam", "id": "504230"}, {"platform": "gog", "id": "1"}],
            )
        )
        self.save(record(self.me, "g6", "Mystery Box", [variant("f", "/g/mystery", platform="Strange Box 9000")]))
        self.cards = gaming_api.cards(self.library(), self.me)

    def run_query(self, query=None, members=None):
        return gaming_query.run(self.cards, query, members)

    def ids(self, answer):
        return [g["id"] for g in answer["games"]]


class CardTest(QueryCase):
    def test_a_card_has_its_systems_and_how_often_it_was_played(self):
        by_id = {c["id"]: c for c in self.cards}
        self.assertEqual((by_id["g1"]["systems"], by_id["g2"]["systems"]), (["windows"], ["pc"]))
        self.assertEqual(by_id["g1"]["playcount"], 3)
        self.assertEqual((by_id["g3"]["systems"], by_id["g4"]["systems"]), (["psx"], ["ps2"]))
        self.assertEqual(by_id["g6"]["systems"], ["strange-box-9000"])
        self.assertEqual(by_id["g2"]["playcount"], 0)

    def test_a_game_with_no_platform_is_on_the_unknown_system(self):
        self.save(record(self.me, "g7", "No Platform", [variant("g", "/g/none")]))
        cards = gaming_api.cards(self.library(), self.me)
        self.assertEqual(next(c for c in cards if c["id"] == "g7")["systems"], ["unknown"])

    def test_the_game_systems_say_the_droidtop_system_beside_the_platform(self):
        listed = {s["name"]: s for s in gaming_api.systems(self.cards)}
        # The id and name that get_game_systems always had stay; the droidtop system comes beside them.
        self.assertEqual((listed["Windows"]["id"], listed["Windows"]["system_id"]), ("windows", "windows"))
        self.assertEqual((listed["Linux"]["system_id"], listed["Linux"]["system_name"]), ("pc", "PC"))
        self.assertEqual(listed["Sony PlayStation 2"]["system_id"], "ps2")
        self.assertEqual(listed["Strange Box 9000"]["known"], False)
        self.assertEqual(listed["Strange Box 9000"]["system_id"], "strange-box-9000")


class QueryTest(QueryCase):
    def test_no_query_gives_every_game_by_title_and_the_facets_count_them_all(self):
        answer = self.run_query()
        self.assertEqual(answer["total"], 6)
        self.assertEqual(self.ids(answer), ["g5", "g3", "g4", "g2", "g1", "g6"])
        facets = answer["facets"]
        self.assertEqual({r["value"]: r["count"] for r in facets["runner"]}, {
            "duckstation": 1, "linux": 1, "pcsx2": 1, "wine": 1,
        })
        self.assertEqual({r["value"]: r["count"] for r in facets["store"]}, {"gog": 1, "steam": 2})
        self.assertEqual({r["value"]: r["count"] for r in facets["installed"]}, {True: 5, False: 1})
        self.assertEqual({r["value"]: r["count"] for r in facets["favourite"]}, {False: 6})

    def test_the_years_are_counted_by_decade_with_the_games_that_have_none(self):
        years = self.run_query()["facets"]["year"]
        self.assertEqual(
            years,
            [
                {"from": 1990, "to": 1999, "count": 2},
                {"from": 2000, "to": 2009, "count": 2},
                {"from": 2010, "to": 2019, "count": 1},
                {"unknown": True, "count": 1},
            ],
        )

    def test_text_finds_every_word_in_the_title_ignoring_case(self):
        self.assertEqual(self.ids(self.run_query({"text": "moonlit"})), ["g2", "g1"])
        self.assertEqual(self.ids(self.run_query({"text": "RACER 2"})), ["g4"])
        self.assertEqual(self.run_query({"text": "nothing like it"})["total"], 0)

    def test_a_facet_counts_leave_out_its_own_filter_and_keep_the_others(self):
        answer = self.run_query({"system": "windows", "runner": "wine"})
        self.assertEqual(self.ids(answer), ["g1"])
        # What each runner would give with the system filter kept: only wine has a game on Windows.
        self.assertEqual({r["value"]: r["count"] for r in answer["facets"]["runner"]}, {"wine": 1})
        # And the system counts keep the runner filter and leave their own out: only wine's game, on Windows.
        self.assertEqual({r["id"]: r["count"] for r in answer["facets"]["system"]}, {"windows": 1})

    def test_text_narrows_every_facet(self):
        answer = self.run_query({"text": "crash"})
        self.assertEqual({r["value"]: r["count"] for r in answer["facets"]["runner"]}, {"duckstation": 1, "pcsx2": 1})

    def test_the_year_range_includes_both_ends_and_leaves_out_games_with_no_year(self):
        self.assertEqual(self.ids(self.run_query({"year_from": 1999, "year_to": 2002})), ["g4", "g1"])
        self.assertEqual(self.ids(self.run_query({"year_from": 2004})), ["g5", "g2"])
        self.assertNotIn("g6", self.ids(self.run_query({"year_to": 2100})))

    def test_installed_and_favourite_are_exact(self):
        self.assertEqual(self.ids(self.run_query({"installed": False})), ["g5"])
        self.assertEqual(self.run_query({"favourite": True})["total"], 0)

    def test_a_favourite_is_found_by_its_mark(self):
        from pythia import favourites

        favourites.set_favourite("g2", True, tell_handhelds=False)
        self.cards = gaming_api.cards(self.library(), self.me)
        self.assertEqual(self.ids(self.run_query({"favourite": True})), ["g2"])

    def test_store_and_system_take_a_list_and_match_any(self):
        self.assertEqual(self.ids(self.run_query({"store": ["gog", "nothing"]})), ["g5"])
        self.assertEqual(self.ids(self.run_query({"system": ["psx", "ps2"]})), ["g3", "g4"])
        self.assertEqual(self.ids(self.run_query({"system": "pc"})), ["g2"])
        self.assertEqual(self.ids(self.run_query({"system": ["windows", "pc"]})), ["g5", "g2", "g1"])

    def test_a_collection_is_a_filter_by_its_members(self):
        answer = self.run_query({"sort": "title"}, members={"g1", "g3"})
        self.assertEqual(self.ids(answer), ["g3", "g1"])
        self.assertEqual({r["value"]: r["count"] for r in answer["facets"]["runner"]}, {"duckstation": 1, "wine": 1})

    def test_the_order_and_the_page(self):
        self.assertEqual(self.ids(self.run_query({"sort": "title", "descending": True})), ["g6", "g1", "g2", "g4", "g3", "g5"])
        self.assertEqual(self.ids(self.run_query({"sort": "playcount", "descending": True}))[0], "g1")
        self.assertEqual(self.ids(self.run_query({"sort": "last_played", "descending": True}))[0], "g2")
        # Games with no value for the sort come last whichever way it runs.
        self.assertEqual(self.ids(self.run_query({"sort": "year"}))[-1], "g6")
        self.assertEqual(self.ids(self.run_query({"sort": "year", "descending": True}))[-1], "g6")
        page = self.run_query({"offset": 2, "limit": 2})
        self.assertEqual((self.ids(page), page["total"], page["offset"], page["limit"]), (["g4", "g2"], 6, 2, 2))
        counts_only = self.run_query({"limit": 0})
        self.assertEqual((counts_only["games"], counts_only["total"]), ([], 6))

    def test_facets_can_be_left_out(self):
        self.assertNotIn("facets", self.run_query({"facets": False}))

    def test_a_query_that_cannot_be_answered_says_which_part(self):
        for bad, word in (
            ({"sort": "colour"}, "sort"),
            ({"limit": -1}, "limit"),
            ({"installed": "yes"}, "installed"),
            ({"year_from": "1990"}, "year_from"),
            ({"system": 3}, "system"),
            ({"facets": 1}, "facets"),
        ):
            with self.assertRaises(gaming_query.QueryError) as caught:
                self.run_query(bad)
            self.assertIn(word, str(caught.exception))
        with self.assertRaises(gaming_query.QueryError):
            gaming_query.run(self.cards, ["not", "an", "object"])

    def test_one_pass_reads_no_record_or_file(self):
        with (
            mock.patch("pythia.datadir.list_all_entries", side_effect=AssertionError("read a record")),
            mock.patch("pathlib.Path.stat", side_effect=AssertionError("read a file")),
        ):
            self.assertEqual(self.run_query({"text": "moonlit"})["total"], 2)
