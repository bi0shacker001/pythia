"""Detection quirks from the Windows UI test (Droidtop/tracker#538):
`.stfolder` was taken as a game, and "0.8.09r1" lost its "1"."""

import tempfile
from pathlib import Path
from unittest import TestCase, mock

from pythia import onboarding, scanning


class VersionTest(TestCase):
    def split(self, name):
        return onboarding._derive_name_version_mods_language(Path("/games") / name)

    def test_letters_then_digits_stay_in_the_version(self):
        for folder, version in (
            ("Lost Game-0.8.09r1", "0.8.09r1"),
            ("Game 1.0b2-pc", "1.0b2"),
            ("Game-v2.0rc3", "2.0rc3"),
        ):
            name, got, mods, _ = self.split(folder)
            self.assertEqual(got, version, folder)
            self.assertNotIn("1", [m["name"] for m in mods])

    def test_plain_and_lettered_versions_are_unchanged(self):
        self.assertEqual(self.split("Game-1.2-pc")[1], "1.2")
        self.assertEqual(self.split("Game-0.5a")[1], "0.5a")
        name, version, mods, _ = self.split("Game-12.0-scrappy")
        self.assertEqual((name, version, [m["name"] for m in mods]), ("Game", "12.0", ["scrappy"]))


class HiddenFoldersTest(TestCase):
    def test_dot_and_system_folders_are_never_scanned(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in (".stfolder", ".stversions", "$RECYCLE.BIN", "System Volume Information", "Real Game"):
                (root / name).mkdir()
                (root / name / "start.exe").write_text("")
            generic = mock.Mock()
            generic.is_root.return_value = False
            found = []
            with mock.patch.object(
                scanning, "_generic_roots", side_effect=lambda child, g: found.append(child.name) or iter(())
            ):
                list(scanning._discover_roots(root, [], generic))
        self.assertEqual(found, ["Real Game"])
