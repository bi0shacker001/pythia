"""Pythia on Windows: the Linux-only start-up probes stay quiet, and the
architecture is known (Droidtop/tracker#538)."""

from unittest import TestCase, mock

from pythia.util import linux
from pythia.util.graphics import drivers


class WindowsStartupTest(TestCase):
    def test_windows_architecture_names_are_known(self):
        for machine, arch in (("AMD64", "x86_64"), ("ARM64", "aarch64")):
            with mock.patch.object(linux.platform, "machine", return_value=machine):
                self.assertEqual(linux.LINUX_SYSTEM.get_arch(), arch)

    def test_missing_linux_commands_are_not_warned_about_elsewhere(self):
        with (
            mock.patch.object(linux.sys, "platform", "win32"),
            mock.patch.object(linux.shutil, "which", return_value=None),
            mock.patch.object(linux.LinuxSystem, "get_sbin_path", return_value=None),
            mock.patch.object(linux.logger, "warning") as warning,
        ):
            linux.LinuxSystem()
        self.assertFalse([c for c in warning.call_args_list if "not found" in str(c)])

    def test_no_gpu_error_without_sys_class_drm_elsewhere(self):
        with (
            mock.patch.object(drivers.sys, "platform", "win32"),
            mock.patch.object(drivers.os.path, "exists", return_value=False),
            mock.patch.object(drivers.logger, "error") as error,
        ):
            self.assertEqual(list(drivers.get_gpu_cards()), [])
        error.assert_not_called()
