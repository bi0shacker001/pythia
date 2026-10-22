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

    def test_a_computer_shares_no_library_yet(self):
        r = rows({"kind": "computer"})
        self.assertFalse(r["Library, saves and launches"]["syncs"])
        self.assertNotIn("Library", r)
