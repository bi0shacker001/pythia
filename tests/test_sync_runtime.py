"""Real tests for pythia.core.sync.runtime.SyncRuntime -- a background
asyncio event loop thread driven from synchronous callers, the way
pythia/ui/backend.py's Qt Slots will drive SyncService."""

import asyncio
import threading
import unittest

from pythia.core.sync.runtime import SyncRuntime


class SyncRuntimeTests(unittest.TestCase):
    def test_dormant_until_first_use(self):
        runtime = SyncRuntime()
        self.assertFalse(runtime.running)
        # Constructing it must not have spawned a thread.
        self.assertIsNone(runtime._thread)

    def test_call_runs_coroutine_on_background_thread_and_returns_real_result(self):
        runtime = SyncRuntime()
        caller_thread = threading.current_thread()

        async def work():
            return threading.current_thread()

        try:
            worker_thread = runtime.call(work(), timeout=5)
            self.assertTrue(runtime.running)
            self.assertNotEqual(caller_thread, worker_thread)
            self.assertEqual("pythia-sync-runtime", worker_thread.name)
        finally:
            runtime.stop()

    def test_exception_in_coroutine_propagates_through_result(self):
        runtime = SyncRuntime()

        async def boom():
            raise ValueError("real failure")

        try:
            with self.assertRaises(ValueError):
                runtime.call(boom(), timeout=5)
        finally:
            runtime.stop()

    def test_multiple_calls_share_the_same_loop_thread(self):
        runtime = SyncRuntime()

        async def get_loop_id():
            return id(asyncio.get_running_loop())

        try:
            first = runtime.call(get_loop_id(), timeout=5)
            second = runtime.call(get_loop_id(), timeout=5)
            self.assertEqual(first, second)
        finally:
            runtime.stop()

    def test_stop_then_reuse_starts_a_fresh_loop(self):
        runtime = SyncRuntime()

        async def noop():
            return True

        try:
            self.assertTrue(runtime.call(noop(), timeout=5))
            runtime.stop()
            self.assertFalse(runtime.running)
            # Reusable, not single-shot.
            self.assertTrue(runtime.call(noop(), timeout=5))
        finally:
            runtime.stop()

    def test_stop_when_never_started_is_a_real_no_op(self):
        runtime = SyncRuntime()
        runtime.stop()  # must not raise
        self.assertFalse(runtime.running)


if __name__ == "__main__":
    unittest.main()
