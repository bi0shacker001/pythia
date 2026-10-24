"""Play for a game added from a folder (pythia.folder_launch and pythia.play;
Droidtop/tracker#629): Pythia picks the game's own program, natively on
Windows, natively or through Wine elsewhere, and the person can change it."""

import os
import shutil
import tempfile
from pathlib import Path
from unittest import TestCase, mock

from pythia import folder_launch, play


def make(root: Path, files: dict) -> None:
    for rel, data in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


class CandidatesTest(TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_the_game_comes_first_and_helpers_are_left_out(self):
        make(
            self.root,
            {
                "MoonlitHarbor.exe": b"MZ" + b"x" * 100,
                "big-tool.exe": b"MZ" + b"x" * 5000,
                "unins000.exe": b"MZ",
                "UnityCrashHandler64.exe": b"MZ",
                "lib/python.exe": b"MZ",
                "renpy/launcher.exe": b"MZ",
                "readme.txt": b"hi",
            },
        )
        found = [c["exe"] for c in folder_launch.candidates(str(self.root), "Moonlit Harbor")]
        self.assertEqual(found, ["MoonlitHarbor.exe", "big-tool.exe"])

    def test_a_linux_build_is_found_by_suffix_or_by_its_elf_header(self):
        make(self.root, {"Game.sh": b"#!/bin/sh", "bin/game": b"\x7fELFrest", "notes": b"text"})
        kinds = {c["exe"]: c["kind"] for c in folder_launch.candidates(str(self.root))}
        self.assertEqual(kinds, {"Game.sh": "linux", "bin/game": "linux"})

    def test_on_windows_the_exe_runs_natively(self):
        make(self.root, {"Game.exe": b"MZ", "Game.sh": b"#!/bin/sh"})
        with mock.patch.object(folder_launch, "on_windows", return_value=True):
            self.assertEqual(folder_launch.pick(str(self.root), "Game"), {"exe": "Game.exe", "via": "native"})

    def test_elsewhere_a_native_build_first_then_wine(self):
        make(self.root, {"Game.exe": b"MZ", "Game.sh": b"#!/bin/sh"})
        with mock.patch.object(folder_launch, "on_windows", return_value=False):
            self.assertEqual(folder_launch.pick(str(self.root), "Game"), {"exe": "Game.sh", "via": "native"})
        os.remove(self.root / "Game.sh")
        with mock.patch.object(folder_launch, "on_windows", return_value=False):
            with mock.patch.object(folder_launch, "wine", return_value="/usr/bin/wine"):
                self.assertEqual(folder_launch.pick(str(self.root), "Game"), {"exe": "Game.exe", "via": "wine"})
            with mock.patch.object(folder_launch, "wine", return_value=None):
                self.assertIsNone(folder_launch.pick(str(self.root), "Game"))
                self.assertIn("Wine is not installed", folder_launch.why_not(str(self.root), "Game"))

    def test_wine_runs_each_game_in_a_prefix_of_its_own(self):
        make(self.root, {"Game.exe": b"MZ"})
        with (
            mock.patch.object(folder_launch, "wine", return_value="/usr/bin/wine"),
            mock.patch.object(folder_launch, "prefix_for", return_value=self.root / "pfx"),
        ):
            args, env = folder_launch.command(str(self.root), {"exe": "Game.exe", "via": "wine"}, "g1")
        self.assertEqual(args, ["/usr/bin/wine", str(self.root / "Game.exe")])
        self.assertEqual(env["WINEPREFIX"], str(self.root / "pfx"))


class PlayFolderTest(TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        make(self.root, {"Tiny.exe": b"MZ"})
        self.entry = {
            "system_uuid": "me",
            "game_id": "g1",
            "canonical_name": "Tiny",
            "platform_ids": [],
            "versions": [{"version": "1.0", "variants": [{"path": str(self.root)}]}],
        }
        for target, value in (
            (play.datadir, "list_all_entries"),
            (play, "get_system_uuid"),
            (play, "_steam_here"),
            (folder_launch, "on_windows"),
        ):
            patch = mock.patch.object(target, value)
            started = patch.start()
            self.addCleanup(patch.stop)
            if value == "list_all_entries":
                started.return_value = [self.entry]
            elif value == "get_system_uuid":
                started.return_value = "me"
            elif value == "_steam_here":
                started.return_value = False
            else:
                started.return_value = True

    def test_a_folder_game_plays_its_own_program_and_notes_when(self):
        primary = play.state("g1")["primary"]
        self.assertEqual((primary["enabled"], primary["via"], primary["choose"]), (True, "folder", True))
        with (
            mock.patch.object(folder_launch.subprocess, "Popen") as popen,
            mock.patch("pythia.database.games.save_entry_and_sync_cache") as save,
        ):
            self.assertEqual(play.play("g1"), {"status": "ok", "via": "folder"})
        self.assertEqual(popen.call_args.args[0], [str(self.root / "Tiny.exe")])
        self.assertEqual(popen.call_args.kwargs["cwd"], str(self.root))
        self.assertTrue(save.call_args.args[0]["versions"][0]["variants"][0]["lastplayed"])

    def test_an_epic_game_is_sent_to_its_launcher(self):
        self.entry["platform_ids"] = [{"platform": "egs", "id": "Fortnite"}]
        primary = play.state("g1")["primary"]
        self.assertEqual((primary["enabled"], primary["reason"]), (False, play.NO_EPIC))

    def test_the_persons_choice_is_kept_and_checked(self):
        make(self.root, {"tools/Other.exe": b"MZ"})
        with mock.patch("pythia.database.games.save_entry_and_sync_cache"):
            self.assertEqual(play.set_launch("g1", "tools/Other.exe", "native")["status"], "ok")
            self.assertEqual(play.state("g1")["primary"]["launch"], {"exe": "tools/Other.exe", "via": "native"})
            self.assertEqual(play.set_launch("g1", "../outside.exe", "native")["status"], "error")
            self.assertEqual(play.set_launch("g1", "Tiny.exe", "magic")["status"], "error")
            self.assertEqual(play.set_launch("g1", "", "")["status"], "ok")
        self.assertEqual(play.state("g1")["primary"]["launch"]["exe"], "Tiny.exe")
