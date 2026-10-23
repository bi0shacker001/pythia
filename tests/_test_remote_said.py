"""What Play there says for each answer a device gives
(pythia.droidtop.remote.play_said; droidtop-agent DESIGN section 17)."""

from unittest import TestCase

from pythia.droidtop.remote import play_said


class PlaySaidTest(TestCase):
    def test_started_says_how_it_is_shown(self):
        self.assertEqual(
            play_said({"result": "started", "stream": "windowcast"}),
            (True, "Started there; it streams through windowcast."),
        )
        self.assertEqual(play_said({"result": "started"}), (True, "Started there."))

    def test_a_refusal_says_why_and_what_to_do(self):
        started, said = play_said({"result": "not_allowed"})
        self.assertFalse(started)
        self.assertIn("its own Devices page", said)
        self.assertEqual(
            play_said({"result": "failed", "message": "no runner"}), (False, "It could not start there: no runner.")
        )
        self.assertEqual(play_said({"result": "later"}), (False, "That device answered later."))
