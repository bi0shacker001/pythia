"""pythia.modes: agent mode forces manager mode (owner, 2026-10-16)."""

from unittest import TestCase, mock

from pythia import modes


class ManagerModeTest(TestCase):
    def setUp(self):
        self.stored = {}
        patches = [
            mock.patch.object(
                modes.settings,
                "read_bool_setting",
                side_effect=lambda key, default=False: self.stored.get(key, default),
            ),
            mock.patch.object(
                modes.settings, "write_setting", side_effect=lambda key, value: self.stored.__setitem__(key, value)
            ),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def agent(self, on):
        return mock.patch.object(modes, "agent_mode", return_value=on)

    def test_on_by_default(self):
        with self.agent(False):
            self.assertEqual(modes.manager_mode()["on"], True)
            self.assertFalse(modes.manager_mode()["locked"])

    def test_without_the_agent_it_can_be_turned_off_and_on(self):
        with self.agent(False):
            self.assertFalse(modes.set_manager_mode(False)["on"])
            self.assertFalse(modes.manager_mode()["on"])
            self.assertTrue(modes.set_manager_mode(True)["on"])

    def test_agent_mode_forces_it_on_and_locks_it_with_a_reason(self):
        self.stored[modes.MANAGER_SETTING] = False
        with self.agent(True):
            state = modes.manager_mode()
        self.assertEqual((state["on"], state["locked"], state["agent"]), (True, True, True))
        self.assertIn("droidtop agent", state["reason"])

    def test_turning_it_off_in_agent_mode_is_refused_and_changes_nothing(self):
        with self.agent(True):
            with self.assertRaisesRegex(modes.ModeLocked, "droidtop agent"):
                modes.set_manager_mode(False)
            self.assertTrue(modes.set_manager_mode(True)["on"])
        self.assertEqual(self.stored, {modes.MANAGER_SETTING: True})

    def test_the_pages_it_covers_are_reported(self):
        with self.agent(False):
            self.assertEqual(modes.manager_mode()["pages"], list(modes.MANAGER_PAGES))


class AgentModeTest(TestCase):
    def test_no_agent_mode_without_the_droidtop_module(self):
        with mock.patch("pythia.droidtop.available", return_value=False):
            self.assertFalse(modes.agent_mode())

    def test_agent_mode_while_serving_is_chosen_or_still_running(self):
        from pythia.droidtop import service, takeover

        with (
            mock.patch("pythia.droidtop.available", return_value=True),
            mock.patch.object(takeover, "serves", return_value=False),
            mock.patch.object(service, "serving", return_value={"state": "serving"}),
        ):
            self.assertTrue(modes.agent_mode())
        with (
            mock.patch("pythia.droidtop.available", return_value=True),
            mock.patch.object(takeover, "serves", return_value=True),
        ):
            self.assertTrue(modes.agent_mode())
        with (
            mock.patch("pythia.droidtop.available", return_value=True),
            mock.patch.object(takeover, "serves", return_value=False),
            mock.patch.object(service, "serving", return_value={"state": "off"}),
        ):
            self.assertFalse(modes.agent_mode())
