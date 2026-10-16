"""A folder's kind and language from its name (UX review 2026-10-16, A14)."""

import tempfile
from pathlib import Path
from unittest import TestCase, mock

from pythia import datadir, onboarding


def split(name):
    return onboarding._derive_folder(Path("/games") / name)


class ClassifyTest(TestCase):
    def test_a_spanish_translation_is_proposed_as_one(self):
        name, version, d = split("UX Test Novel v1.2 (Spanish translation)")
        self.assertEqual((name, version), ("UX Test Novel", "1.2"))
        self.assertEqual((d["kind"], d["language"], d["mods"], d["flags"]), ("translation", "es", [], []))

    def test_english_translated_is_not_machine_translation(self):
        d = split("UX Test Novel-1.2-English translated")[2]
        self.assertEqual((d["kind"], d["language"], d["flags"]), ("translation", "en", []))

    def test_mtl_only_from_mtl_or_machine(self):
        for folder in ("Game-1.0-mtl", "Game 0.3 Machine Translation"):
            d = split(folder)[2]
            self.assertEqual((d["kind"], d["flags"]), ("translation", ["mtl"]), folder)

    def test_kind_flag_and_platform_words_are_never_mods(self):
        d = split("Game v0.5 [Uncensor Patch]")[2]
        self.assertEqual((d["kind"], d["flags"], d["mods"]), ("patch", ["uncensored"], []))
        d = split("Game-1.1-pc-animated-unc")[2]
        self.assertEqual(([m["name"] for m in d["mods"]], d["flags"]), (["animated"], ["uncensored"]))
        d = split("Game v1.0 (German Mod)")[2]
        self.assertEqual((d["kind"], d["language"], d["mods"]), ("mod", "de", []))

    def test_more_languages_by_name_and_code(self):
        for word, code in (("ru", "ru"), ("Russian", "ru"), ("ptbr", "pt"), ("Deutsch", "de"), ("French", "fr")):
            self.assertEqual(datadir.classify_folder_tokens(word)["language"], code, word)

    def test_a_plain_mod_name_is_still_a_mod(self):
        d = split("Game-12.0-scrappy")[2]
        self.assertEqual(([m["name"] for m in d["mods"]], d["kind"]), (["scrappy"], "game"))

    def test_the_two_value_wrapper_keeps_its_shape(self):
        self.assertEqual(datadir.classify_variant_tokens("(Spanish translation)"), ([], "es"))


class OnboardTest(TestCase):
    def test_onboarding_records_kind_and_flags_and_a_rescan_keeps_the_persons_kind(self):
        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp) / "UX Test Novel v1.2 (Spanish translation)"
            (game / "game").mkdir(parents=True)
            (game / "renpy").mkdir()
            (game / "game" / "script.rpy").write_text("label start:\n")
            saved = []
            with (
                mock.patch.object(onboarding, "get_system_uuid", return_value="me"),
                mock.patch.object(onboarding.datadir, "list_all_entries", return_value=[]),
                mock.patch.object(onboarding.games_db, "save_entry_and_sync_cache", side_effect=saved.append),
            ):
                self.assertEqual(onboarding.onboard(game)["status"], "ok")
            variant = saved[0]["versions"][0]["variants"][0]
            self.assertEqual((variant["kind"], variant["language"], variant["flags"]), ("translation", "es", []))
            variant["kind"], variant["kind_set_by_person"] = "mod", True
            onboarding._merge_version(saved[0], game, "1.2", [], "es", [], {"kind": "renpy"}, kind="translation")
            self.assertEqual(saved[0]["versions"][0]["variants"][0]["kind"], "mod")
