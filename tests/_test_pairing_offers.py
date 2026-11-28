import threading
from unittest import TestCase

from pythia.pairing_offers import PairingChanged, PairingOffers


class PairingOffersTest(TestCase):
    def test_abandoned_new_owner_does_not_poison_the_existing_listener(self):
        offers = PairingOffers()
        active, abandoned = object(), object()
        stopped = []
        offers.begin(active, "compare", lambda: {}, lambda: stopped.append("active"))
        pending = offers.reserve(abandoned, "compare")
        offers.release(abandoned)
        self.assertEqual(
            offers.begin(
                abandoned,
                "compare",
                lambda: self.fail("closed client started"),
                lambda: None,
                lambda: False,
                generation=pending,
            )["status"],
            "error",
        )
        self.assertIsNone(offers.latest(abandoned, "compare"))
        self.assertEqual(
            offers.observe(active, "compare", lambda: {"state": "offering"}, lambda: True)["state"], "offering"
        )
        self.assertEqual(stopped, [])

    def test_release_before_late_reservation_still_removes_the_closed_owner(self):
        offers = PairingOffers()
        active, closed = object(), object()
        offers.begin(active, "compare", lambda: {}, lambda: None)
        offers.release(closed)
        late = offers.reserve(closed, "compare")
        offers.begin(
            closed, "compare", lambda: self.fail("late client started"), lambda: None, lambda: False, generation=late
        )
        self.assertIsNone(offers.latest(closed, "compare"))
        self.assertEqual(
            offers.observe(active, "compare", lambda: {"state": "offering"}, lambda: True)["state"], "offering"
        )

    def test_accepted_takeover_never_revives_an_older_queued_start(self):
        offers = PairingOffers()
        old, newer = object(), object()
        first = offers.reserve(old, "compare")
        offers.begin(newer, "compare", lambda: {}, lambda: None)
        offers.stop(newer, "compare", offers.request_stop(newer, "compare"))
        result = offers.begin(old, "compare", lambda: self.fail("old request revived"), lambda: None, generation=first)
        self.assertEqual(result["status"], "error")

    def test_confirmation_uses_only_the_owners_observed_code(self):
        offers = PairingOffers()
        owner, other = object(), object()
        offers.begin(owner, "compare", lambda: {}, lambda: None)
        generation = offers.latest(owner, "compare")
        with self.assertRaises(PairingChanged):
            offers.prepare(owner, "compare", generation, lambda _: None, lambda: True, needs_code=True)
        seen = offers.observe(owner, "compare", lambda: {"state": "comparing", "code": "123456"}, lambda: True)
        self.assertEqual(seen["code"], "123456")
        self.assertEqual(
            offers.observe(other, "compare", lambda: self.fail("read another owner's code"), lambda: True)["state"],
            "failed",
        )
        expected = []
        operation = object()
        self.assertIs(
            offers.prepare(
                owner,
                "compare",
                generation,
                lambda code: expected.append(code) or operation,
                lambda: True,
                needs_code=True,
            ),
            operation,
        )
        self.assertEqual(expected, ["123456"])
        offers.reserve_pick(owner)
        with self.assertRaises(PairingChanged):
            offers.prepare(
                owner,
                "compare",
                generation,
                lambda _: self.fail("prepared stale confirmation"),
                lambda: True,
                needs_code=True,
            )

    def test_poll_during_a_new_pick_does_not_read_or_cancel_the_old_comparison(self):
        offers = PairingOffers()
        owner = object()
        stopped = []
        offers.begin(owner, "compare", lambda: {}, lambda: stopped.append(True))
        generation = offers.reserve_pick(owner)
        self.assertIsNotNone(generation)
        state = offers.observe(owner, "compare", lambda: self.fail("read superseded comparison"), lambda: True)
        self.assertEqual(state["state"], "finishing")
        self.assertEqual(stopped, [])

    def test_stopped_pick_needs_a_fresh_discovery_or_offer(self):
        offers = PairingOffers()
        owner = object()
        generation = offers.reserve_pick(owner)
        offers.request_stop(owner, "compare")
        self.assertIsNone(offers.reserve_pick(owner))
        offers.begin_discovery(owner)
        next_generation = offers.reserve_pick(owner)
        self.assertGreater(next_generation, generation)
        started = []

        def old_start():
            started.append(True)
            return {}

        self.assertEqual(
            offers.begin(owner, "compare", old_start, lambda: None, generation=generation)["status"], "error"
        )
        self.assertEqual(started, [])

    def test_discovery_does_not_replace_another_windows_offer(self):
        offers = PairingOffers()
        old, current = object(), object()
        offers.reserve_pick(old)
        offers.request_stop(old, "compare")
        offers.begin(current, "compare", lambda: {}, lambda: None)
        offers.begin_discovery(old)
        self.assertEqual(
            offers.observe(current, "compare", lambda: {"state": "offering"}, lambda: True)["state"], "offering"
        )

    def test_delayed_stop_cannot_withdraw_a_new_offer_on_the_same_connection(self):
        offers = PairingOffers()
        owner = object()
        stopped = []
        offers.begin(owner, "compare", lambda: {}, lambda: stopped.append("old"))
        old_stop = offers.request_stop(owner, "compare")
        new_start = offers.reserve(owner, "compare")
        offers.begin(owner, "compare", lambda: {}, lambda: stopped.append("new"), generation=new_start)
        self.assertFalse(offers.stop(owner, "compare", old_stop))
        self.assertEqual(stopped, ["old"])
        offers.release(owner)
        self.assertEqual(stopped, ["old", "new"])

    def test_stop_before_worker_start_prevents_a_late_listener(self):
        offers = PairingOffers()
        owner = object()
        generation = offers.reserve(owner, "compare")
        offers.request_stop(owner, "compare")
        started = []

        def start():
            started.append(True)
            return {"status": "ok"}

        result = offers.begin(owner, "compare", start, lambda: None, generation=generation)
        self.assertEqual(result["status"], "error")
        self.assertEqual(started, [])

    def test_reordered_old_start_does_not_replace_a_newer_clients_listener(self):
        offers = PairingOffers()
        old, new = object(), object()
        first = offers.reserve(old, "compare")
        second = offers.reserve(new, "compare")
        stopped = []
        offers.begin(new, "compare", lambda: {}, lambda: stopped.append("new"), generation=second)
        result = offers.begin(old, "compare", lambda: {}, lambda: stopped.append("old"), generation=first)
        self.assertEqual(result["status"], "error")
        self.assertEqual(stopped, [])

    def test_disconnect_withdraws_only_its_current_offer(self):
        offers = PairingOffers()
        old, new = object(), object()
        stopped = []
        offers.begin(old, "compare", lambda: {"status": "ok"}, lambda: stopped.append("old"))
        offers.begin(new, "compare", lambda: {"status": "ok"}, lambda: stopped.append("new"))
        offers.release(old)
        self.assertEqual(stopped, ["old"])
        offers.release(new)
        self.assertEqual(stopped, ["old", "new"])

    def test_old_client_cannot_stop_a_new_qt_offer(self):
        offers = PairingOffers()
        client = object()
        stopped = []
        offers.begin(client, "compare", lambda: {}, lambda: stopped.append("client"))
        offers.begin(None, "code", lambda: {}, lambda: stopped.append("qt"))
        offers.release(client)
        self.assertFalse(offers.stop(client, "code", offers.request_stop(client, "code")))
        self.assertEqual(stopped, ["client"])
        self.assertTrue(offers.stop(None, "code", offers.request_stop(None, "code")))
        self.assertEqual(stopped, ["client", "qt"])

    def test_disconnect_during_start_withdraws_the_late_offer(self):
        offers = PairingOffers()
        owner = object()
        entered, complete, disconnected = threading.Event(), threading.Event(), threading.Event()
        stopped, result = [], []

        def start():
            entered.set()
            if not complete.wait(5):
                raise TimeoutError("test start was never released")
            return {"status": "ok"}

        worker = threading.Thread(
            target=lambda: result.append(
                offers.begin(owner, "compare", start, lambda: stopped.append(True), lambda: not disconnected.is_set())
            )
        )
        worker.start()
        self.assertTrue(entered.wait(5))
        disconnected.set()
        complete.set()
        worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result[0]["status"], "error")
        offers.release(owner)
        self.assertEqual(stopped, [True])

    def test_a_closed_client_never_starts_listening(self):
        offers = PairingOffers()
        started = []

        def start():
            started.append(True)
            return {"status": "ok"}

        result = offers.begin(object(), "compare", start, lambda: None, lambda: False)
        self.assertEqual(result["status"], "error")
        self.assertEqual(started, [])

    def test_failed_start_is_withdrawn_and_does_not_own_a_new_offer(self):
        offers = PairingOffers()
        stopped = []

        def broken():
            raise RuntimeError("listener started but setup failed")

        with self.assertRaises(RuntimeError):
            offers.begin(object(), "compare", broken, lambda: stopped.append(True))
        self.assertEqual(stopped, [True])
