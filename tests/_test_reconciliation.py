"""reconciliation.find_merge_candidates: which pairs are one game by
droidtop's rule and which are only alike (Droidtop/tracker#538 retest B2)."""

from unittest import TestCase, mock

from pythia import reconciliation


def entry(game_id, name, path):
    return {
        "game_id": game_id,
        "canonical_name": name,
        "versions": [{"version": "1.0", "variants": [{"path": path, "source": {"kind": "renpy"}}]}],
    }


class FindMergeCandidatesTest(TestCase):
    def reasons(self, entries):
        with mock.patch.object(reconciliation, "_own_entries", return_value=entries):
            found = reconciliation.find_merge_candidates()
        return {(c["a"]["name"], c["b"]["name"]): c["reason"] for c in found}

    def test_equal_name_keys_are_the_same_game(self):
        found = self.reasons([entry("a", "GoodbyeEternity", "/g/a"), entry("b", "Goodbye Eternity", "/h/b")])
        self.assertEqual(found, {("GoodbyeEternity", "Goodbye Eternity"): "same"})

    def test_names_only_alike_are_a_suggestion(self):
        found = self.reasons([entry("a", "Game", "/g/a"), entry("b", "Beta Game", "/h/b"), entry("c", "Zzz", "/i/c")])
        self.assertEqual(found, {("Game", "Beta Game"): "name"})

    def test_name_key_is_droidtops(self):
        self.assertEqual(reconciliation.name_key("Love of Magic: Book 1!"), "loveofmagicbook1")
