"""Paths are checked in the dialog before anything starts (UX review
2026-10-16, A2): pythia.path_check gives the reason a path will not do, which
the Add, Scan, Watch and Export dialogs show under the field."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import TestCase, mock

from pythia import path_check


class PathCheckTest(TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_a_readable_folder_will_do_for_every_folder_want(self):
        for want in ("game", "folder", "writable"):
            self.assertEqual(path_check.check(str(self.tmp), want), "", want)

    def test_a_missing_path_says_nothing_is_there(self):
        missing = self.tmp / "nope"
        for want in ("game", "folder", "writable", "program"):
            self.assertIn("Nothing is at", path_check.check(str(missing), want), want)

    def test_an_empty_path_asks_for_one_except_for_the_optional_program(self):
        self.assertIn("Browse", path_check.check("  ", "folder"))
        self.assertEqual(path_check.check("", "program"), "")

    def test_a_relative_path_is_refused(self):
        self.assertIn("whole path", path_check.check("games", "folder"))

    def test_a_file_is_not_a_folder_but_an_archive_is_a_game(self):
        plain = self.tmp / "readme.txt"
        plain.write_text("x")
        archive = self.tmp / "Game-1.0.zip"
        archive.write_bytes(b"PK")
        self.assertIn("not a folder", path_check.check(str(plain), "folder"))
        self.assertIn(".zip", path_check.check(str(plain), "game"))
        self.assertEqual(path_check.check(str(archive), "game"), "")
        self.assertIn("not a folder", path_check.check(str(archive), "folder"))

    def test_a_program_is_a_file_that_can_run(self):
        program = self.tmp / "tool"
        program.write_text("#!/bin/sh\n")
        if sys.platform != "win32":
            self.assertIn("not marked", path_check.check(str(program), "program"))
            program.chmod(0o755)
        self.assertEqual(path_check.check(str(program), "program"), "")
        self.assertIn("folder, not a program", path_check.check(str(self.tmp), "program"))

    def test_home_is_expanded(self):
        with mock.patch.dict(os.environ, {"HOME": str(self.tmp), "USERPROFILE": str(self.tmp)}):
            self.assertEqual(path_check.check("~", "folder"), "")

    @unittest.skipIf(sys.platform == "win32" or os.geteuid() == 0, "permissions are not enforced here")
    def test_an_unreadable_or_unwritable_folder_says_so(self):
        locked = self.tmp / "locked"
        locked.mkdir()
        locked.chmod(0o500)
        self.addCleanup(locked.chmod, 0o700)
        self.assertEqual(path_check.check(str(locked), "folder"), "")
        self.assertIn("cannot write", path_check.check(str(locked), "writable"))
        locked.chmod(0o000)
        self.assertIn("cannot read", path_check.check(str(locked), "folder"))

    def test_an_unknown_want_is_a_mistake(self):
        with self.assertRaises(ValueError):
            path_check.check(str(self.tmp), "anything")


class PlaceholdersTest(TestCase):
    def test_every_path_prompt_has_an_os_shaped_hint_and_a_check(self):
        app = (Path(__file__).parents[1] / "pythia/ui/desktop/web/app.js").read_text(encoding="utf-8")
        prompts = [line for line in app.splitlines() if "kind: 'path'" in line]
        self.assertGreaterEqual(len(prompts), 6)
        for line in prompts:
            self.assertNotIn("hint: 'C:", line)
            self.assertNotIn("hint: 'G:", line)
            self.assertIn("check: checkPath(", line)


class HandOffTest(TestCase):
    """UX review A3: every hand-off out of Pythia goes through handOff (asks first in the handheld
    shell; on desktop only a launcher's first use asks), and none is a primary button."""

    def test_open_path_open_url_and_open_in_manager_are_called_only_through_hand_off(self):
        app = (Path(__file__).parents[1] / "pythia/ui/desktop/web/app.js").read_text(encoding="utf-8")
        for name in ("'open_path'", "'open_url'", "pythia.open_in_manager("):
            for line in app.splitlines():
                if name in line:
                    self.assertTrue("handOff(" in line or "run: () =>" in line, line[:160])
        self.assertNotIn('class="btn primary" type="button">Open in', app)
        self.assertNotIn('class="hh-action primary" ${m.available', app)


class HandheldSelectsTest(TestCase):
    """UX review A5: no native <select> in the handheld shell (a D-pad cannot open one); its
    choices are cycleRow buttons that Left and Right change."""

    def test_handheld_pages_draw_no_select(self):
        web = Path(__file__).parents[1] / "pythia/ui/desktop/web"
        app = (web / "app.js").read_text(encoding="utf-8")
        start, end = app.index("function renderHandheld("), app.index("function hhKeepFocus(")
        self.assertNotIn("<select", app[start:end])
        html = (web / "index.html").read_text(encoding="utf-8")
        shell = html[html.index('id="handheldShell"') :]
        self.assertNotIn("<select", shell)
