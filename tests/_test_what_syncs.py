"""What syncs with a paired device (UX review AG6): generated from the sync
model, and game files never sync."""

from unittest import TestCase

from pythia.droidtop.syncs import SYNCTHING, what_syncs


def rows(device, settings=None):
    return {r["what"]: r for r in what_syncs(device, settings or {})}


class WhatSyncsTest(TestCase):
    def test_game_files_never_sync_with_any_device(self):
        for kind in ("handheld", "computer"):
            game_files = rows({"kind": kind})["Game files"]
            self.assertFalse(game_files["syncs"])
            self.assertIn("never sync", game_files["detail"])

    def test_a_handheld_follows_its_settings(self):
        r = rows(
            {"kind": "handheld", "primary": True, "launch": False, "announced": {SYNCTHING: {"syncthing": "X"}}},
            {"approved": {"f95": {}}, "syncthing": True},
        )
        self.assertTrue(r["Library"]["syncs"])
        self.assertIn("this handheld's copy wins", r["Saves"]["detail"])
        self.assertFalse(r["Starting games here"]["syncs"])
        self.assertTrue(r["Plugin data"]["syncs"])
        self.assertTrue(r["Syncthing folders"]["syncs"])

    def test_syncthing_off_here_or_not_offered_there(self):
        announced = {"kind": "handheld", "announced": {SYNCTHING: {"syncthing": "X"}}}
        self.assertFalse(rows(announced, {"syncthing": False})["Syncthing folders"]["syncs"])
        self.assertFalse(rows({"kind": "handheld"})["Syncthing folders"]["syncs"])

    def test_a_computer_is_offered_nothing_until_the_person_offers_it(self):
        r = rows({"kind": "computer"})
        self.assertFalse(r["Library"]["syncs"])
        self.assertFalse(r["Saves"]["syncs"])
        self.assertFalse(r["Starting games here"]["syncs"])
        self.assertFalse(r["Plugin data"]["syncs"])
        r = rows({"kind": "computer", "offered": {"library": True, "saves": False}}, {"approved": {"f95": {}}})
        self.assertTrue(r["Library"]["syncs"])
        self.assertFalse(r["Saves"]["syncs"])
        # Plugin data never syncs between two computers, whatever is approved.
        self.assertFalse(r["Plugin data"]["syncs"])

    def test_a_handheld_follows_what_it_is_offered(self):
        r = rows({"kind": "handheld", "offered": {"library": True, "saves": False}})
        self.assertTrue(r["Library"]["syncs"])
        self.assertFalse(r["Saves"]["syncs"])
        self.assertIn("does not offer it its saves", r["Saves"]["detail"])
        # One from an agent that does not say gets a handheld's default.
        self.assertTrue(rows({"kind": "handheld"})["Saves"]["syncs"])
