"""pythia.util.display's compositor checks where their programs are missing
(Droidtop/tracker#538 retest W3: "Unable to run command, pgrep not found" on
every start on Windows)."""

from unittest import TestCase, mock

from pythia.util import display


class CompositorCheckTest(TestCase):
    def test_a_missing_check_program_finds_no_compositor_and_logs_nothing(self):
        with (
            mock.patch.dict(display.os.environ, {"DESKTOP_SESSION": ""}),
            mock.patch.object(display.shutil, "which", return_value=None),
            mock.patch.object(display.subprocess, "Popen") as popen,
            mock.patch.object(display.logger, "error") as error,
        ):
            self.assertFalse(display.is_compositing_enabled())
        popen.assert_not_called()
        error.assert_not_called()

    def test_a_running_compositor_is_still_found(self):
        with (
            mock.patch.dict(display.os.environ, {"DESKTOP_SESSION": ""}),
            mock.patch.object(display.shutil, "which", return_value="/usr/bin/pgrep"),
            mock.patch.object(display, "_get_command_output", return_value=b"1234\n"),
        ):
            self.assertTrue(display.is_compositing_enabled())
