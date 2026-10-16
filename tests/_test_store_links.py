"""Store links on a game or a folder, and the update check that uses them
(UX review 2026-10-16, A15)."""

from unittest import TestCase, mock

from pythia import store_links, versions
from pythia.plugins.library import f95_updates


class ParseTest(TestCase):
    def test_links_and_ids_people_paste(self):
        cases = {
            "https://f95zone.to/threads/ux-test-novel-v1-2-dev.123456/": ("f95", "123456"),
            "f95zone.to/threads/987": ("f95", "987"),
            "https://store.steampowered.com/app/620/Portal_2/": ("steam", "620"),
            "https://www.gog.com/en/game/the_witcher": ("gog", "the_witcher"),
            "https://dev.itch.io/some-game": ("itch", "https://dev.itch.io/some-game"),
            "RJ01234567": ("dlsite", "RJ01234567"),
            "https://www.dlsite.com/maniax/work/=/product_id/rj123456.html": ("dlsite", "RJ123456"),
        }
        for text, (platform, ident) in cases.items():
            self.assertEqual(store_links.parse(text), {"platform": platform, "id": ident}, text)

    def test_anything_else_is_refused_with_what_it_takes(self):
        for text in ("", "12345", "https://example.com/game"):
            with self.assertRaises(store_links.LinkError):
                store_links.parse(text)

    def test_a_store_without_an_update_check_says_so(self):
        dl = store_links.describe({"platform": "dlsite", "id": "RJ01234567"})
        self.assertEqual(
            (dl["store"], dl["update_check"], dl["note"]), ("DLsite", False, "No update check for this store yet.")
        )
        self.assertTrue(store_links.describe({"platform": "f95", "id": "1"})["update_check"])

    def test_one_link_per_store_on_a_holder(self):
        holder = {}
        store_links.put(holder, {"platform": "f95", "id": "1"})
        store_links.put(holder, {"platform": "f95", "id": "2"})
        store_links.put(holder, {"platform": "dlsite", "id": "RJ1"})
        self.assertEqual(holder["platform_ids"], [{"platform": "f95", "id": "2"}, {"platform": "dlsite", "id": "RJ1"}])
        self.assertTrue(store_links.remove(holder, "f95"))
        self.assertFalse(store_links.remove(holder, "f95"))


class VersionsTest(TestCase):
    def test_same_order_as_droidtop(self):
        self.assertGreater(versions.compare_loose("0.10", "0.9"), 0)
        self.assertGreater(versions.compare_loose("1.2", "1.1.9"), 0)
        self.assertLess(versions.compare_loose("", "0.1"), 0)
        self.assertEqual(versions.latest(["1.1", "1.2", "1.0"]), "1.2")
        self.assertIsNone(versions.latest(["1.0", "Final"]))
        self.assertEqual(versions.in_use({"current_version": "1.1", "versions": [{"version": "1.2"}]}), "1.1")


def entry(platform_ids=(), groups=()):
    return {
        "system_uuid": "me",
        "game_id": "g1",
        "canonical_name": "UX Test Novel",
        "platform_ids": list(platform_ids),
        "available_updates": [],
        "versions": list(groups),
    }


class UpdateCheckTest(TestCase):
    def run_check(self, entries, threads):
        saved = []
        with (
            mock.patch("pythia.datadir.list_all_entries", return_value=entries),
            mock.patch("pythia.identity.get_system_uuid", return_value="me"),
            mock.patch("pythia.database.games.save_entry_and_sync_cache", side_effect=saved.append),
            mock.patch.object(f95_updates, "fast_check", return_value={t: 100 for t in threads}) as fast,
            mock.patch.object(f95_updates, "full_check", side_effect=lambda t, _ts: {"version": threads[t]}) as full,
        ):
            result = f95_updates.run_update_check()
        return result, saved, fast, full

    def test_a_folder_added_game_linked_to_a_thread_is_checked(self):
        game = entry(
            platform_ids=[{"platform": "f95", "id": "111"}],
            groups=[
                {"version": "1.0", "variants": [{"path": "/g/1.0", "source": {"kind": "renpy"}}]},
                {"version": "1.2", "variants": [{"path": "/g/1.2", "source": {"kind": "renpy"}}]},
            ],
        )
        result, saved, fast, _full = self.run_check([game], {111: "1.3"})
        self.assertEqual(fast.call_args.args[0], [111])
        self.assertEqual((result["checked"], result["updated"]), (1, 1))
        update = saved[-1]["available_updates"][0]
        self.assertEqual((update["version"], update.get("for")), ("1.3", None))

    def test_a_translation_with_its_own_thread_is_checked_against_that_thread(self):
        game = entry(
            platform_ids=[{"platform": "f95", "id": "111"}],
            groups=[
                {"version": "1.2", "variants": [{"path": "/g/1.2", "source": {"kind": "renpy"}}]},
                {
                    "version": "0.9",
                    "variants": [
                        {
                            "path": "/g/es",
                            "kind": "translation",
                            "source": {"kind": "renpy"},
                            "platform_ids": [{"platform": "f95", "id": "222"}],
                        }
                    ],
                },
            ],
        )
        result, saved, fast, full = self.run_check([game], {111: "1.2", 222: "1.0"})
        self.assertEqual(sorted(fast.call_args.args[0]), [111, 222])
        self.assertEqual(sorted(c.args[0] for c in full.call_args_list), [111, 222])
        updates = saved[-1]["available_updates"]
        # The base game (1.2) is up to date with thread 111; the translation (0.9) is behind its own thread 222.
        self.assertEqual([(u["version"], u.get("for")) for u in updates], [("1.0", "/g/es")])
        self.assertEqual(result["updated"], 1)
