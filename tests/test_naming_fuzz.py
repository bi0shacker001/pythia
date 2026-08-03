"""
Fuzz/property tests for the naming heuristic (onboarding.py) -- stdlib only
(unittest + random), no external dependency, matching this project's minimal-
dependency policy. Checks crash-safety and structural invariants, not semantic
correctness: `_derive_name_version_mods_language` is explicitly a best-effort
placeholder (see its own docstring), expected to misclassify some real folder names
-- that's an accepted tradeoff, not a bug this test should chase. What it must never
do is throw, or hand back a shape the rest of onboarding.py doesn't expect.

Run: `python -m unittest tests.test_naming_fuzz -v` (from the pythia/ package root).
`FUZZ_SEED` env var pins the random seed for a reproducible failure to debug against;
otherwise a fresh seed is picked and printed so a CI failure can be replayed locally.
"""

import os
import random
import string
import unittest
from pathlib import Path

from pythia.datadir import classify_variant_tokens
from pythia.onboarding import (
    _derive_name_version_mods_language,
    _extract_version_only,
    _find_meaningful_ancestor_name,
    _is_generic_part_leaf,
)

_ITERATIONS = 2000

# Building blocks real folder names actually draw from, plus deliberately hostile
# characters (unicode, path separators, control chars, empty pieces) mixed in --
# the fuzzer assembles folder names by joining a random selection of these, not by
# generating pure noise, since pure noise rarely exercises the regexes' branch
# points the way a "plausible-shaped" adversarial string does.
_WORD_PARTS = [
    "BeingADik", "Fetish_Locator", "love of magic", "Cyberpunk", "Far Cry",
    "AHouseInTheRift", "Bunnycop", "MGQ Paradox", "60 Parsecs!",
    "Café Stella and the Reaper's Butterflies", "7 Days to Die", "", " ", "-", "_",
    "Chap3+", "Week", "Part", "Episode", "Vol", "v", ".", "..", "...",
    "日本語", "한국어", "中文", "🎮", "\x00", "﻿", "‽", "™", "©",
]
_VERSION_PARTS = [
    "1", "0.8.3", "10.0", "v1.2.3", "1_3_3", "049", "2077", "5", "6", "9.5",
    "0.8.09r1", "-", "_", ".", "v", "", "999999999999999999999999999999",
]
_TAG_PARTS = [
    "ENG", "eng", "en", "jp", "cn", "mtl", "UNCENSORED", "scrappy", "pc",
    "win64", "+", "!!!", "", " ", "\t", "\n",
]


def _random_folder_name(rng: random.Random) -> str:
    pool = _WORD_PARTS + _VERSION_PARTS + _TAG_PARTS
    pieces = [rng.choice(pool) for _ in range(rng.randint(0, 6))]
    sep = rng.choice(["-", "_", " ", ""])
    name = sep.join(pieces)
    if rng.random() < 0.1:
        name = "".join(rng.choice(string.printable) for _ in range(rng.randint(0, 40)))
    return name


def _assert_mods_shape(test: unittest.TestCase, mods, context: str):
    test.assertIsInstance(mods, list, context)
    for mod in mods:
        test.assertIsInstance(mod, dict, context)
        test.assertIn("name", mod, context)
        test.assertIsInstance(mod["name"], str, context)
        test.assertIn("version", mod, context)
        test.assertTrue(mod["version"] is None or isinstance(mod["version"], str), context)


def _assert_language_shape(test: unittest.TestCase, language, context: str):
    test.assertTrue(language is None or isinstance(language, str), context)


class NamingFuzzTest(unittest.TestCase):
    def setUp(self):
        seed = int(os.environ.get("FUZZ_SEED", random.randrange(2**32)))
        self._seed = seed
        self.rng = random.Random(seed)
        # Printed unconditionally (not just on failure) -- unittest's internal
        # per-test result/outcome attributes aren't a stable public API to probe
        # from tearDown, and printing every run is harmless, cheap noise.
        print(f"\n[{self._testMethodName}] FUZZ_SEED={seed} (set this env var to reproduce)")

    def test_derive_name_version_mods_language_never_crashes(self):
        for _ in range(_ITERATIONS):
            leaf = _random_folder_name(self.rng)
            # A couple of parent levels too, since the ancestor-walk path
            # (_find_meaningful_ancestor_name) is only exercised when there's
            # something to walk up into.
            parent = _random_folder_name(self.rng)
            grandparent = _random_folder_name(self.rng)
            path = Path(grandparent or "x") / (parent or "y") / (leaf or "z")

            name, version, mods, language = _derive_name_version_mods_language(path)

            ctx = f"path={path!r}"
            self.assertIsInstance(name, str, ctx)
            self.assertTrue(name != "", f"name must never be empty -- {ctx}")
            self.assertIsInstance(version, str, ctx)
            _assert_mods_shape(self, mods, ctx)
            _assert_language_shape(self, language, ctx)

    def test_classify_variant_tokens_never_crashes(self):
        for _ in range(_ITERATIONS):
            rest = _random_folder_name(self.rng)
            mods, language = classify_variant_tokens(rest)
            ctx = f"rest={rest!r}"
            _assert_mods_shape(self, mods, ctx)
            _assert_language_shape(self, language, ctx)

    def test_is_generic_part_leaf_never_crashes(self):
        for _ in range(_ITERATIONS):
            name = _random_folder_name(self.rng)
            result = _is_generic_part_leaf(name)
            self.assertIsInstance(result, bool, f"name={name!r}")

    def test_extract_version_only_never_crashes(self):
        for _ in range(_ITERATIONS):
            leaf = _random_folder_name(self.rng)
            version, mods, language = _extract_version_only(leaf)
            ctx = f"leaf={leaf!r}"
            # version may legitimately be "" (no version info determined) --
            # _derive_name_version_mods_language's own non-generic-leaf branch
            # returns "" for version too, so an empty string is an established,
            # valid sentinel here, not something to reject.
            self.assertIsInstance(version, str, ctx)
            _assert_mods_shape(self, mods, ctx)
            _assert_language_shape(self, language, ctx)

    def test_find_meaningful_ancestor_name_never_crashes(self):
        for _ in range(_ITERATIONS):
            parts = [_random_folder_name(self.rng) or "x" for _ in range(self.rng.randint(1, 5))]
            path = Path(*parts)
            result = _find_meaningful_ancestor_name(path, max_levels=self.rng.randint(0, 4))
            self.assertTrue(result is None or isinstance(result, str), f"path={path!r}")

    def test_extreme_length_input(self):
        # A folder name far longer than anything real, to catch pathological regex
        # backtracking (ReDoS-shaped bugs) rather than just "does it crash."
        import time

        for filler in ("a", "1", "-", " ", "v1.2.3-"):
            leaf = filler * 5000
            start = time.monotonic()
            _derive_name_version_mods_language(Path(leaf))
            elapsed = time.monotonic() - start
            self.assertLess(elapsed, 2.0, f"pathological slowdown on filler={filler!r} took {elapsed:.2f}s")


# A small fixed corpus of real names actually seen in the user's library this
# session, kept as regression cases -- not exhaustive, just the ones already known
# to have mattered (see the plan's naming-heuristic hardening notes).
_REAL_CASES = [
    "10.0-sancho",
    "Fetish Locator",
    "Week 1",
    "Far Cry 5",
    "Far Cry 6",
    "Cyberpunk 2077",
    "love_of_magic_book1_v1_3_3_win64",
    "AHouseInTheRift-0.8.09r1-pc",
    "9.5-ENG",
    "Bunnycop-049",
    "To Be A King - Volume 1",
    "MGQ Paradox 3.06",
    "Chap3+",
]


class NamingRealCorpusTest(unittest.TestCase):
    def test_real_corpus_never_crashes_and_keeps_shape(self):
        for leaf in _REAL_CASES:
            path = Path("some_parent") / leaf
            name, version, mods, language = _derive_name_version_mods_language(path)
            ctx = f"leaf={leaf!r}"
            self.assertIsInstance(name, str, ctx)
            self.assertTrue(name != "", ctx)
            self.assertIsInstance(version, str, ctx)
            _assert_mods_shape(self, mods, ctx)
            _assert_language_shape(self, language, ctx)


if __name__ == "__main__":
    unittest.main()
