"""A game Pythia onboarded from a folder reads as installed, with its folder,
platform and a slug (Droidtop/tracker#538: `pythia -l` showed them empty)."""

from unittest import TestCase

from pythia.database import games

ENTRY = {
    "system_uuid": "me",
    "game_id": "82bd303d44bf48b9",
    "canonical_name": "Test Quest",
    "versions": [
        {
            "version": "1.0",
            "variants": [
                {
                    "id": "249dd4e42330",
                    "path": "/games/Test Quest-1.0-pc",
                    "platforms": ["linux-x86_64"],
                    "source": {"kind": "renpy"},
                    "detected_at": "2026-10-15T04:00:25+00:00",
                }
            ],
        }
    ],
}


class OnboardedRowTest(TestCase):
    def test_a_folder_tracked_game_reads_as_installed_with_its_folder(self):
        variant = games._primary_variant(ENTRY)
        row = games._merged_row(ENTRY, variant)
        self.assertEqual(row["directory"], "/games/Test Quest-1.0-pc")
        self.assertTrue(row["installed"])
        self.assertEqual(row["platform"], "linux-x86_64")
        self.assertEqual(row["slug"], "test-quest")
        self.assertIsNone(row["runner"])  # an engine is not a runner

    def test_lutris_shaped_fields_are_kept(self):
        entry = {
            **ENTRY,
            "slug": "tq",
            "versions": [{"version": "1", "variants": [{"directory": "/d", "installed": False}]}],
        }
        row = games._merged_row(entry, games._primary_variant(entry))
        self.assertEqual((row["slug"], row["directory"], row["installed"]), ("tq", "/d", False))
