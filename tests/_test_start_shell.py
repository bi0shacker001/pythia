"""Start in, kept by Pythia, and handhelds found by the device (UX review
2026-10-16, A8)."""

import tempfile
from pathlib import Path
from unittest import TestCase, mock

from pythia import start_shell


class Settings:
    def __init__(self):
        self.values = {}

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value


class HandheldTest(TestCase):
    def test_known_handhelds_by_maker_and_model(self):
        for maker, model in [
            ("GPD", "G1618-04"),  # Win 4
            ("AYANEO", "AYANEO 2S"),
            ("AYN", "Loki MiniPro"),
            ("ONE-NETBOOK", "ONEXPLAYER X1"),
            ("Valve", "Jupiter"),
            ("Valve", "Galileo"),
            ("ASUSTeK COMPUTER INC.", "ROG Ally RC71L_RC71L"),
            ("ASUSTeK COMPUTER INC.", "RC72LA"),
            ("LENOVO", "83E1"),
            ("Micro-Star International Co., Ltd.", "Claw A1M"),
        ]:
            self.assertTrue(start_shell.handheld(maker, model), (maker, model))

    def test_other_pcs_from_the_same_makers_are_not(self):
        for maker, model in [
            ("ASUSTeK COMPUTER INC.", "ROG Strix G614"),
            ("LENOVO", "21HD"),  # a ThinkPad
            ("Micro-Star International Co., Ltd.", "MS-7D75"),
            ("Valve", "Index"),
            ("", ""),
        ]:
            self.assertFalse(start_shell.handheld(maker, model), (maker, model))

    def test_detect_reads_dmi_on_linux(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "sys_vendor").write_text("GPD\n")
            Path(tmp, "product_name").write_text("G1618-04\n")
            with mock.patch.object(start_shell, "DMI", Path(tmp)), mock.patch("sys.platform", "linux"):
                self.assertEqual(start_shell.detect(), {"handheld": True, "device": "GPD G1618-04"})
        with mock.patch.object(start_shell, "DMI", Path("/no/such/dmi")), mock.patch("sys.platform", "linux"):
            self.assertEqual(start_shell.detect(), {"handheld": False, "device": None})


class StartInTest(TestCase):
    def setUp(self):
        self.settings = Settings()
        for name, fn in (("get_setting", self.settings.get), ("set_setting", self.settings.set)):
            patcher = mock.patch.object(start_shell, name, side_effect=fn)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_auto_follows_the_device(self):
        self.assertEqual(start_shell.start_in(), "auto")
        self.assertEqual(start_shell.initial_shell({"handheld": True, "device": "GPD"}), "handheld")
        self.assertEqual(start_shell.initial_shell({"handheld": False, "device": None}), "desktop")

    def test_a_fixed_choice_wins_and_is_kept_by_pythia(self):
        start_shell.set_start_in("handheld")
        self.assertEqual(self.settings.values["start_in"], "handheld")
        self.assertEqual(start_shell.initial_shell({"handheld": False, "device": None}), "handheld")
        with self.assertRaises(ValueError):
            start_shell.set_start_in("bigpicture")

    def test_last_is_only_what_a_confirmed_switch_remembered(self):
        start_shell.set_start_in("last")
        self.assertEqual(start_shell.initial_shell({"handheld": True, "device": "GPD"}), "desktop")
        start_shell.remember("handheld")
        self.assertEqual(start_shell.initial_shell({"handheld": False, "device": None}), "handheld")
        with self.assertRaises(ValueError):
            start_shell.remember("web")


class PageTest(TestCase):
    def test_one_start_control_and_no_big_picture(self):
        web = Path(__file__).parents[1] / "pythia/ui/desktop/web"
        html = (web / "index.html").read_text(encoding="utf-8")
        app = (web / "app.js").read_text(encoding="utf-8")
        self.assertEqual(html.count("<strong>Start in</strong>"), 1)
        self.assertNotIn("Interface on start", html)
        self.assertNotIn("Big picture", html + app)
        self.assertNotIn("localStorage.setItem('pythia-startup-shell'", app)
