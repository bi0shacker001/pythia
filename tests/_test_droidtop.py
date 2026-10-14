"""pythia.droidtop: the library Pythia hands the droidtop agent, and the call
wrapper, without the compiled agent module."""

import json
import os
import tempfile
from unittest import TestCase, mock

from pythia.droidtop import agent, library


class TitleKeyTest(TestCase):
    def test_matches_the_agents_title_key(self):
        # droidtop-agent core library::title_key's own example.
        self.assertEqual(library.title_key("The Game: Deluxe!"), "title:the game deluxe")
        self.assertEqual(library.title_key("the  game deluxe"), "title:the game deluxe")
        self.assertEqual(library.title_key("Café Stella"), "title:café stella")


class ScannedGamesTest(TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.old = os.path.join(self.dir.name, "Eternum-0.9.4-pc")
        self.new = os.path.join(self.dir.name, "Eternum-0.9.5-pc")
        os.makedirs(self.old)
        os.makedirs(self.new)

    def tearDown(self):
        self.dir.cleanup()

    def entry(self, **extra):
        e = {
            "system_uuid": "me",
            "canonical_name": "Eternum",
            "platform_ids": [],
            "versions": [
                {"version": "0.9.4", "variants": [{"path": self.old, "detected_at": "2026-10-01T00:00:00"}]},
                {"version": "0.9.5", "variants": [{"path": self.new, "detected_at": "2026-10-02T00:00:00"}]},
            ],
        }
        e.update(extra)
        return e

    def test_the_newest_install_with_a_folder_is_the_game(self):
        (game,) = library.scanned_games([self.entry()], "me")
        self.assertEqual(game["key"], "title:eternum")
        self.assertEqual(game["install"]["version"], "0.9.5")
        self.assertEqual(game["base"], self.new)
        self.assertEqual(game["install"]["launcher"], "pythia")

    def test_store_ids_are_the_agents_keys(self):
        steam = self.entry(platform_ids=[{"platform": "f95", "id": "1"}, {"platform": "steam", "id": "440"}])
        egs = self.entry(platform_ids=[{"platform": "egs", "id": "Fortnite"}])
        self.assertEqual([g["key"] for g in library.scanned_games([steam, egs], "me")], ["steam:440", "epic:Fortnite"])

    def test_other_systems_and_missing_folders_are_left_out(self):
        gone = self.entry(versions=[{"version": "1", "variants": [{"path": os.path.join(self.dir.name, "gone")}]}])
        self.assertEqual(library.scanned_games([self.entry(system_uuid="other"), gone], "me"), [])

    def test_a_version_that_repeats_the_title_is_no_version(self):
        e = self.entry(versions=[{"version": "Eternum", "variants": [{"path": self.new}]}])
        (game,) = library.scanned_games([e], "me")
        self.assertNotIn("version", game["install"])


class CallTest(TestCase):
    def test_refusals_raise_with_the_agents_words(self):
        fake = mock.Mock()
        fake.call.return_value = json.dumps({"error": "No paired handheld has that id."})
        with mock.patch.object(agent, "_module", fake):
            with self.assertRaisesRegex(agent.AgentError, "No paired handheld"):
                agent.unpair("ab")
        fake.call.assert_called_once_with("unpair", json.dumps({"id": "ab"}))

    def test_without_the_module_it_says_why(self):
        with mock.patch.object(agent, "_module", None), mock.patch.object(agent, "_missing", "not installed"):
            self.assertFalse(agent.available())
            with self.assertRaisesRegex(agent.AgentError, "not installed"):
                agent.identity()
