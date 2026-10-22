"""Service mode (docs/SPEC.md 1.10, windowcast#467): what Settings shows, and
the elevated copy's arguments."""

from unittest import TestCase, mock

from pythia import modes, secure_screen


class ServiceModeTest(TestCase):
    def test_off_windows_it_is_off_and_says_why(self):
        with mock.patch.object(secure_screen, "state", return_value="not_available"):
            svc = modes.service_mode()
        self.assertFalse(svc["on"])
        self.assertFalse(svc["available"])
        self.assertEqual(svc["reason"], "Service mode is only on Windows.")

    def test_states_in_words(self):
        for key, on, installed in (("absent", False, False), ("disabled", False, True), ("enabled", True, True)):
            with (
                mock.patch.object(secure_screen, "state", return_value=key),
                mock.patch.object(secure_screen, "consent", return_value="What it does."),
            ):
                svc = modes.service_mode()
            self.assertEqual((svc["on"], svc["installed"], svc["available"]), (on, installed, True))
            self.assertEqual(svc["consent"], "What it does.")


class ElevatedCopyTest(TestCase):
    def native(self):
        fake = mock.Mock()
        fake.secure_screen.perform.return_value = 0
        return fake

    def test_enable_carries_the_account_and_the_helper(self):
        fake = self.native()
        with (
            mock.patch.object(secure_screen, "_native", fake),
            mock.patch.object(secure_screen.HELPER.__class__, "is_file", return_value=True),
        ):
            code = secure_screen.main(["enable", "--confirmed", "--grant-sid", "S-1-5-21-1-2-3-1001"])
        self.assertEqual(code, 0)
        fake.secure_screen.perform.assert_called_once_with(
            "enable", grant_sid="S-1-5-21-1-2-3-1001", helper=str(secure_screen.HELPER)
        )

    def test_anything_else_is_refused_without_a_change(self):
        fake = self.native()
        with mock.patch.object(secure_screen, "_native", fake):
            for argv in (["enable"], ["disable", "--confirmed", "--grant-sid"], ["disable", "--yes"], []):
                self.assertEqual(secure_screen.main(argv), 3, argv)
        fake.secure_screen.perform.assert_not_called()

    def test_the_change_reruns_this_module_isolated(self):
        fake = self.native()
        fake.secure_screen.request.return_value = "Service mode is on."
        with mock.patch.object(secure_screen, "_native", fake):
            self.assertEqual(secure_screen.change("enable"), "Service mode is on.")
        action, program, args = fake.secure_screen.request.call_args.args
        self.assertEqual((action, args), ("enable", ["-I", "-m", "pythia.secure_screen"]))
