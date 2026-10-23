"""A second Look does not have the first's progress or result written over
it (Droidtop/tracker#538 retest: "starting a second Look while one runs makes
the first's progress and result overwrite the page and the stored result")."""

from unittest import TestCase

from pythia.look import LookSlot


class LookSlotTest(TestCase):
    def test_the_newer_look_keeps_the_page(self):
        slot = LookSlot()
        slot.start("first", "/games/first")
        slot.start("second", "/games/second")
        self.assertIsNone(slot.progress("first", {"folders": 9, "games": 9}))
        self.assertEqual(slot.progress("second", {"folders": 1, "games": 1})["root"], "/games/second")
        self.assertTrue(slot.finish("second", {"status": "ok", "root": "/games/second", "job": "second"}))
        self.assertFalse(slot.finish("first", {"status": "ok", "root": "/games/first", "job": "first"}))
        self.assertFalse(slot.fail("first"))
        self.assertEqual(slot.get()["root"], "/games/second")

    def test_one_look_runs_as_before(self):
        slot = LookSlot()
        self.assertEqual(slot.start("a", "/g")["status"], "looking")
        self.assertEqual(slot.progress("a", {"folders": 2, "games": 1})["counts"], {"folders": 2, "games": 1})
        self.assertTrue(slot.fail("a"))
        self.assertIsNone(slot.get())
