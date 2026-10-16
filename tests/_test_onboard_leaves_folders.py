"""Onboarding never writes into a game's folder (Droidtop/tracker#538, the
Windows UI test: onboarding wrote `.pythia-instance` into every game folder).
The install's identity is its entry in Pythia's own data."""

import json
import tempfile
from pathlib import Path
from unittest import TestCase, mock

from pythia import onboarding
from pythia.core import instance_marker


def listing(root: Path) -> dict:
    return {str(p.relative_to(root)): p.stat().st_mtime_ns for p in sorted(root.rglob("*"))}


class OnboardLeavesFoldersTest(TestCase):
    def onboard(self, folder: Path, entries=()):
        saved = []
        with (
            mock.patch.object(onboarding, "get_system_uuid", return_value="me"),
            mock.patch.object(onboarding.datadir, "list_all_entries", return_value=list(entries)),
            mock.patch.object(
                onboarding.games_db, "save_entry_and_sync_cache", side_effect=lambda e: saved.append(e) or "x.json"
            ),
        ):
            result = onboarding.onboard(folder)
        return result, saved

    def test_a_folder_is_left_exactly_as_it_was(self):
        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp) / "Lost Game-1.2-pc"
            (game / "game").mkdir(parents=True)
            (game / "renpy").mkdir()
            (game / "game" / "script.rpy").write_text("label start:\n")
            before = listing(game)
            result, saved = self.onboard(game)
            self.assertEqual(result["status"], "ok", result)
            self.assertEqual(listing(game), before)
            self.assertFalse(instance_marker.marker_path(game).exists())
        entry = saved[0]
        self.assertTrue(entry["game_uuid"])
        paths = [v["path"] for g in entry["versions"] for v in g["variants"]]
        self.assertEqual(paths, [str(game.resolve())])

    def test_a_marker_an_earlier_pythia_wrote_is_still_read_and_left_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp) / "Lost Game-1.2-pc"
            (game / "game").mkdir(parents=True)
            (game / "renpy").mkdir()
            (game / "game" / "script.rpy").write_text("label start:\n")
            instance_marker.marker_path(game).write_text(json.dumps({"game_uuid": "u-1"}))
            known = {
                "system_uuid": "me",
                "game_id": "g1",
                "game_uuid": "u-1",
                "canonical_name": "Lost Game",
                "versions": [{"version": "1.1", "variants": [{"path": str(Path(tmp) / "old")}]}],
            }
            before = listing(game)
            result, _ = self.onboard(game, entries=[known])
            self.assertEqual(result["game_id"], "g1")
            self.assertEqual(listing(game), before)
