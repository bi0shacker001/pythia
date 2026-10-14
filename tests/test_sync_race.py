"""Real tests for pythia.core.sync.service.race_candidates() -- bounded
concurrent racing with real per-candidate error retention. No network:
`attempt` is a plain async callable under test control, same as any other
pure-orchestration test in this suite.
"""

import asyncio
import unittest

from pythia.core.sync import service


class RaceCandidatesTests(unittest.IsolatedAsyncioTestCase):
    async def test_simultaneous_success_closes_completed_loser(self):
        class Connection:
            def __init__(self):
                self.closed = False

            async def close(self):
                self.closed = True

        connections = {}

        async def attempt(candidate):
            connection = Connection()
            connections[candidate] = connection
            return connection

        winner, result = await service.race_candidates(["a:1", "b:2"], attempt, timeout_per_attempt=5)
        self.assertIs(connections[winner], result)
        losers = [connection for candidate, connection in connections.items() if candidate != winner]
        self.assertEqual(1, len(losers))
        self.assertTrue(losers[0].closed)

    async def test_first_success_wins_and_cancels_the_rest(self):
        started = []
        cancelled = []

        async def attempt(candidate):
            started.append(candidate)
            if candidate == "fast:1":
                return "connection-fast"
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                cancelled.append(candidate)
                raise
            return f"connection-{candidate}"

        winner, result = await service.race_candidates(["fast:1", "slow:2", "slow:3"], attempt, timeout_per_attempt=5)
        self.assertEqual("fast:1", winner)
        self.assertEqual("connection-fast", result)
        # The slow ones should have been cancelled, not left running.
        for _ in range(20):
            if len(cancelled) == 2:
                break
            await asyncio.sleep(0.05)
        self.assertEqual(2, len(cancelled))

    async def test_all_failures_raise_with_every_real_per_candidate_error(self):
        async def attempt(candidate):
            raise ConnectionRefusedError(f"refused by {candidate}")

        with self.assertRaises(service.RaceAllFailedError) as ctx:
            await service.race_candidates(["a:1", "b:2"], attempt, timeout_per_attempt=5)
        self.assertEqual({"a:1", "b:2"}, set(ctx.exception.errors))
        self.assertIn("refused by a:1", ctx.exception.errors["a:1"])

    async def test_empty_candidate_list_raises_immediately(self):
        async def attempt(candidate):
            self.fail("attempt() must never be called for an empty candidate list")

        with self.assertRaises(service.RaceAllFailedError):
            await service.race_candidates([], attempt)

    async def test_bounded_concurrency_never_exceeds_max_concurrent(self):
        in_flight = 0
        peak = 0

        async def attempt(candidate):
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            try:
                await asyncio.sleep(0.1)
            finally:
                in_flight -= 1
            raise ConnectionError("never succeeds")

        with self.assertRaises(service.RaceAllFailedError):
            await service.race_candidates([f"c{i}:1" for i in range(10)], attempt, max_concurrent=2, timeout_per_attempt=5)
        self.assertLessEqual(peak, 2)

    async def test_slow_candidate_times_out_as_a_real_per_candidate_error(self):
        async def attempt(candidate):
            await asyncio.sleep(10)
            return "never"

        with self.assertRaises(service.RaceAllFailedError) as ctx:
            await service.race_candidates(["slow:1"], attempt, timeout_per_attempt=0.1)
        self.assertIn("slow:1", ctx.exception.errors)

    async def test_slower_success_after_faster_failure_still_wins(self):
        async def attempt(candidate):
            if candidate == "bad:1":
                raise ConnectionError("bad")
            await asyncio.sleep(0.2)
            return "connected"

        winner, result = await service.race_candidates(["bad:1", "good:2"], attempt, timeout_per_attempt=5)
        self.assertEqual("good:2", winner)
        self.assertEqual("connected", result)

    async def test_cancelling_the_race_itself_cancels_pending_attempts(self):
        cancelled = []

        async def attempt(candidate):
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                cancelled.append(candidate)
                raise

        race_task = asyncio.ensure_future(service.race_candidates(["a:1", "b:2"], attempt, timeout_per_attempt=5))
        await asyncio.sleep(0.1)
        race_task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await race_task
        for _ in range(20):
            if len(cancelled) == 2:
                break
            await asyncio.sleep(0.05)
        self.assertEqual(2, len(cancelled))


class RaceStaggerTests(unittest.IsolatedAsyncioTestCase):
    """Happy Eyeballs-style connection-attempt stagger (2026-08-09,
    "internal IPv6 direct connectivity and dual-stack route selection") --
    opt-in via `stagger_delay`, default 0 so every pre-existing
    race_candidates() caller/test above keeps its exact prior (all-attempts-
    start-at-once) behavior."""

    async def test_default_stagger_is_zero_all_attempts_start_immediately(self):
        start_times = []

        async def attempt(candidate):
            start_times.append(asyncio.get_event_loop().time())
            raise ConnectionError("never succeeds")

        with self.assertRaises(service.RaceAllFailedError):
            await service.race_candidates(["a:1", "b:2", "c:3"], attempt, timeout_per_attempt=5)
        spread = max(start_times) - min(start_times)
        self.assertLess(spread, 0.05)

    async def test_positive_stagger_delays_successive_attempt_starts(self):
        start_times = []

        async def attempt(candidate):
            start_times.append(asyncio.get_event_loop().time())
            raise ConnectionError("never succeeds")

        with self.assertRaises(service.RaceAllFailedError):
            await service.race_candidates(
                ["a:1", "b:2", "c:3"], attempt, timeout_per_attempt=5, stagger_delay=0.1, max_concurrent=3
            )
        self.assertEqual(3, len(start_times))
        start_times.sort()
        self.assertGreaterEqual(start_times[1] - start_times[0], 0.08)
        self.assertGreaterEqual(start_times[2] - start_times[1], 0.08)

    async def test_a_fast_early_winner_cancels_a_still_delayed_candidate_before_it_ever_starts(self):
        """A candidate whose stagger delay hasn't elapsed yet must never
        open a socket at all once the race is already won -- nothing left
        to drain for it."""
        started = []

        async def attempt(candidate):
            started.append(candidate)
            if candidate == "fast:1":
                return "connected"
            await asyncio.sleep(10)

        winner, result = await service.race_candidates(
            ["fast:1", "slow:2"], attempt, timeout_per_attempt=5, stagger_delay=1.0, max_concurrent=2
        )
        self.assertEqual("fast:1", winner)
        await asyncio.sleep(0.05)
        # "slow:2" was still sleeping in its stagger delay (1s) when the
        # race was won almost instantly -- its attempt() must never have
        # been entered.
        self.assertEqual(["fast:1"], started)


if __name__ == "__main__":
    unittest.main()
