"""A game's cover from what is on this computer (pythia.covers): Lutris's
cover art by the game's slug, else Steam's library art by its Steam id."""

import tempfile
from pathlib import Path
from unittest import TestCase, mock

from pythia import covers


class CoversTest(TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.art = self.dir / "coverart"
        self.steam = self.dir / "librarycache"
        self.art.mkdir()
        self.steam.mkdir()
        covers._steam_cache.cache_clear()
        patches = [
            mock.patch.object(covers.settings, "COVERART_PATH", str(self.art)),
            mock.patch.object(covers, "_steam_cache", lambda: self.steam),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_lutris_cover_art_by_slug_comes_first(self):
        (self.art / "moonlit-harbor.jpg").write_bytes(b"jpg")
        (self.steam / "440_library_600x900.jpg").write_bytes(b"jpg")
        found = covers.cover_for("Moonlit Harbor", [{"platform": "steam", "id": "440"}])
        self.assertEqual(found, self.art / "moonlit-harbor.jpg")

    def test_steam_art_for_a_steam_game_in_either_layout(self):
        (self.steam / "440_library_600x900.jpg").write_bytes(b"jpg")
        (self.steam / "620").mkdir()
        (self.steam / "620" / "library_600x900.jpg").write_bytes(b"jpg")
        tf2 = covers.cover_for("TF2", [{"platform": "steam", "id": "440"}])
        portal = covers.cover_for("Portal 2", [{"platform": "steam", "id": 620}])
        self.assertEqual(tf2, self.steam / "440_library_600x900.jpg")
        self.assertEqual(portal, self.steam / "620" / "library_600x900.jpg")

    def test_no_cover_is_none_and_the_page_draws_its_own(self):
        self.assertIsNone(covers.cover_for("Lantern Road", [{"platform": "f95", "id": "123"}]))
        self.assertIsNone(covers.cover_url([{"canonical_name": "Lantern Road", "platform_ids": []}]))

    def test_the_url_is_a_file_url(self):
        (self.art / "ironvale.png").write_bytes(b"png")
        self.assertEqual(covers.cover_url([{"canonical_name": "Ironvale"}]), (self.art / "ironvale.png").as_uri())
