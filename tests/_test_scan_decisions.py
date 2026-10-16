"""A scan's decisions (pythia.scanning): the folder ends up in the library once,
and the decision card shows what was detected (Droidtop/tracker#538, the
Windows UI test: a resolved decision left a duplicate record, and the card
read "Engine: Not identified, Version: none" for a Ren'Py 1.2 folder)."""

from unittest import TestCase, mock

from pythia import scanning


def scan_result(game_id="new1"):
    return {
        "status": "ok",
        "game_id": game_id,
        "path": "/data/datadir/x.json",
        "game_path": "/games/Lost Game-1.2-pc",
        "name": "Lost Game",
        "candidates": {"certain": None, "suggested": [{"game_id": "old1", "canonical_name": "Lost Game"}]},
    }


class DecisionForTest(TestCase):
    def test_the_card_gets_the_detection_version_and_the_scans_own_record(self):
        pv = {"status": "ok", "detection": {"kind": "renpy", "platforms": []}, "version_label": "1.2"}
        with mock.patch.object(scanning.onboarding, "preview", return_value=pv):
            d = scanning.decision_for(scan_result())
        self.assertEqual(d["path"], "/games/Lost Game-1.2-pc")
        self.assertEqual(d["detection"]["kind"], "renpy")
        self.assertEqual(d["version_label"], "1.2")
        self.assertEqual(d["recorded_game_id"], "new1")
        self.assertEqual(d["candidates"]["suggested"][0]["game_id"], "old1")

    def test_a_folder_that_cannot_be_previewed_keeps_the_rest(self):
        with mock.patch.object(scanning.onboarding, "preview", return_value={"status": "error", "message": "gone"}):
            d = scanning.decision_for(scan_result())
        self.assertNotIn("detection", d)
        self.assertEqual(d["recorded_game_id"], "new1")


class ResolveDecisionTest(TestCase):
    def test_a_new_game_answer_keeps_the_scans_record(self):
        self.assertTrue(scanning.keeps_scan_record({"action": "new"}, "new1"))
        self.assertFalse(scanning.keeps_scan_record({"action": "link", "game_id": "old1"}, "new1"))
        self.assertFalse(scanning.keeps_scan_record({"action": "new"}, None))

    def test_linking_removes_the_scans_record(self):
        with (
            mock.patch.object(scanning.onboarding, "onboard", return_value={"status": "ok", "game_id": "old1"}),
            mock.patch("pythia.database.games.delete_game") as delete,
        ):
            r = scanning.resolve_decision("/games/x", {"action": "link", "game_id": "old1"}, "new1")
        self.assertEqual(r["game_id"], "old1")
        delete.assert_called_once_with("new1")

    def test_a_failed_link_removes_nothing(self):
        with (
            mock.patch.object(scanning.onboarding, "onboard", return_value={"status": "error", "message": "no"}),
            mock.patch("pythia.database.games.delete_game") as delete,
        ):
            scanning.resolve_decision("/games/x", {"action": "link", "game_id": "old1"}, "new1")
        delete.assert_not_called()

    def test_linked_to_its_own_record_removes_nothing(self):
        with (
            mock.patch.object(scanning.onboarding, "onboard", return_value={"status": "ok", "game_id": "new1"}),
            mock.patch("pythia.database.games.delete_game") as delete,
        ):
            scanning.resolve_decision("/games/x", {"action": "link", "game_id": "new1"}, "new1")
        delete.assert_not_called()
