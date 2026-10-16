"""Look, Review, Add (UX review 2026-10-16, A16): Look writes nothing and
groups folders into games by title, with versions, parts and translations."""

import tempfile
from pathlib import Path
from unittest import TestCase, mock

from pythia import look, onboarding


def renpy(folder: Path):
    (folder / "game").mkdir(parents=True)
    (folder / "renpy").mkdir()
    (folder / "game" / "script.rpy").write_text("label start:\n")


class LookTest(TestCase):
    def test_the_test_library_groups_into_three_games_and_nothing_is_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            lib = Path(tmp)
            for v in (
                "UX Test Novel v1.0",
                "UX Test Novel v1.1",
                "UX Test Novel v1.2",
                "UX Test Novel v1.2 (Spanish translation)",
            ):
                renpy(lib / "Versions" / "UX Test Novel" / v)
            for p in ("Part1", "Part2", "Part3"):
                renpy(lib / "Multi-part" / "UX Test Serial" / p)
            renpy(lib / "UX Test Lone Novel")
            counts = []
            with (
                mock.patch.object(onboarding, "get_system_uuid", return_value="me"),
                mock.patch.object(onboarding.datadir, "list_all_entries", return_value=[]),
                mock.patch.object(
                    onboarding.games_db, "save_entry_and_sync_cache", side_effect=AssertionError("wrote")
                ),
                mock.patch("pythia.datadir.save_entry", side_effect=AssertionError("wrote"), create=True),
            ):
                result = look.look(lib, progress=counts.append)
        by_name = {p["name"]: p for p in result["proposals"]}
        self.assertEqual(sorted(by_name), ["UX Test Lone Novel", "UX Test Novel", "UX Test Serial"])
        novel, serial = by_name["UX Test Novel"], by_name["UX Test Serial"]
        self.assertEqual(
            (novel["versions"], novel["translations"], len(novel["folders"])), (["1.0", "1.1", "1.2"], 1, 4)
        )
        spanish = next(f for f in novel["folders"] if "Spanish" in f["path"])
        self.assertEqual((spanish["kind"], spanish["language"], spanish["version"]), ("translation", "es", "1.2"))
        self.assertEqual((serial["parts"], serial["versions"]), (["Part1", "Part2", "Part3"], []))
        self.assertEqual(result["folders"], 8)
        self.assertEqual(
            result["summary"], "8 folders, 3 games (4 are folders of UX Test Novel; 3 are parts of UX Test Serial)"
        )
        self.assertEqual(counts[-1], {"folders": 8, "games": 3})

    def test_a_game_already_in_the_library_is_matched_with_its_reason(self):
        folders = [
            {
                "path": "/l/UX Test Novel v1.3",
                "name": "UX Test Novel",
                "version": "1.3",
                "part": None,
                "kind": "game",
                "language": None,
                "flags": [],
                "mods": [],
                "engine": "renpy",
                "platforms": [],
                "candidates": {"certain": None, "suggested": [{"game_id": "g1", "name": "UX Test Novel"}]},
            }
        ]
        (proposal,) = look.group(folders)
        self.assertEqual(proposal["match"]["game_id"], "g1")
        self.assertIn("already in the library", proposal["match"]["reason"])


class MixedFolderTest(TestCase):
    """UX review A17: a mixed folder keeps its unidentified games when scanned from above."""

    def test_loose_games_beside_a_recognised_one_are_found_from_above(self):
        with tempfile.TemporaryDirectory() as tmp:
            lib = Path(tmp) / "library"
            renpy(lib / "Versions" / "UX Test Novel" / "UX Test Novel v1.0")
            renpy(lib / "Loose" / "UX Test Lone Novel")
            godot = lib / "Loose" / "UX Test Godot Demo"
            godot.mkdir(parents=True)
            (godot / "UX Test Godot Demo.exe").write_bytes(b"MZ")
            (godot / "UX Test Godot Demo.pck").write_bytes(b"GDPC")
            rpgm = lib / "Loose" / "UX Test RPG Maker Quest"
            (rpgm / "www" / "data").mkdir(parents=True)
            (rpgm / "Game.exe").write_bytes(b"MZ")
            unknown = lib / "Loose" / "UX Test Unknown Program"
            unknown.mkdir(parents=True)
            (unknown / "program.exe").write_bytes(b"MZ")
            empty = lib / "Loose" / "Screenshots"
            empty.mkdir(parents=True)
            (empty / "shot.png").write_bytes(b"\\x89PNG")
            with (
                mock.patch.object(onboarding, "get_system_uuid", return_value="me"),
                mock.patch.object(onboarding.datadir, "list_all_entries", return_value=[]),
            ):
                from_above = look.look(lib)
                direct = look.look(lib / "Loose")
        names = lambda r: sorted(Path(f["path"]).name for p in r["proposals"] for f in p["folders"])  # noqa: E731
        loose = ["UX Test Godot Demo", "UX Test Lone Novel", "UX Test RPG Maker Quest", "UX Test Unknown Program"]
        self.assertEqual(names(direct), loose)
        self.assertEqual(names(from_above), sorted(loose + ["UX Test Novel v1.0"]))
        self.assertIn("Screenshots: no game found in it.", [s["message"] for s in from_above["skipped"]])
