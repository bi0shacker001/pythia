"""Game controllers come from the backend, and only while Pythia has the
focus (docs/SPEC.md 2.7; UX review 2026-10-16, A7)."""

from pathlib import Path
from unittest import TestCase, mock

from pythia import pad
from pythia.droidtop import native


class FakeNative:
    def __init__(self, queued):
        self.queued = list(queued)
        self.started = 0

    def start(self):
        self.started += 1

    def poll(self):
        out, self.queued = self.queued, []
        return out

    def pads(self):
        return [{"id": 0, "name": "Xbox Controller"}]


class DeliverTest(TestCase):
    def test_actions_reach_the_page_while_pythia_has_the_focus(self):
        sent = []
        self.assertEqual(pad.deliver(["down", "a", "nonsense"], True, sent.append), 2)
        self.assertEqual(sent, ["down", "a"])

    def test_a_press_while_another_app_has_the_focus_does_nothing(self):
        sent = []
        self.assertEqual(pad.deliver(["a", "b", "start"], False, sent.append), 0)
        self.assertEqual(sent, [])


class PadReaderTest(TestCase):
    def reader(self, queued, focused):
        sent = []
        fake = FakeNative(queued)
        reader = pad.PadReader(sent.append, focused=lambda: focused)
        with mock.patch.object(native, "submodule", return_value=fake), mock.patch("PySide6.QtCore.QTimer"):
            self.assertTrue(reader.start())
        return reader, fake, sent

    def test_drained_presses_are_dropped_not_kept_while_unfocused(self):
        reader, fake, sent = self.reader(["a", "down"], focused=False)
        reader.drain()
        self.assertEqual(sent, [])
        # Dropped, not queued: focusing Pythia later does not replay them.
        reader._focused = lambda: True
        reader.drain()
        self.assertEqual(sent, [])
        fake.queued = ["b"]
        reader.drain()
        self.assertEqual(sent, ["b"])
        self.assertEqual(reader.pads(), [{"id": 0, "name": "Xbox Controller"}])

    def test_without_the_native_module_it_says_why(self):
        reader = pad.PadReader(lambda a: None, focused=lambda: True)
        with mock.patch.object(native, "submodule", side_effect=native.AgentError("not built")):
            self.assertFalse(reader.start())
        self.assertIn("not built", reader.problem)
        self.assertEqual(reader.pads(), [])


class PageTest(TestCase):
    def test_the_page_does_not_read_the_gamepad_api(self):
        app = (Path(__file__).parents[1] / "pythia/ui/desktop/web/app.js").read_text(encoding="utf-8")
        self.assertNotIn("getGamepads", app)
        self.assertIn("pythia.padAction.connect(hhPadAction)", app)

    def test_the_focus_ring_has_its_own_colour_in_every_theme(self):
        import re

        tokens = (Path(__file__).parents[1] / "pythia/ui/desktop/web/tokens.css").read_text(encoding="utf-8")
        blocks = re.findall(r'html\[data-theme="(\w+)"\]\{(.*?)\n\}', tokens, re.S)
        self.assertEqual(len(blocks), 3)
        for theme, body in blocks:
            ring = re.search(r"--focus-ring:(#[0-9a-f]+)", body).group(1)
            accent = re.search(r"--accent:(#[0-9a-f]+)", body).group(1)
            self.assertNotEqual(ring, accent, theme)
