"""Background work + idle/timer scheduling -- GTK removal (2026-08-04): this used
to be built directly on GLib's main loop (GLib.idle_add/timeout_add/MainContext),
which requires PyGObject and a running GLib loop, neither of which the Qt-based
Pythia UI has. Reimplemented on plain threading.Timer instead.

Real, honest limitation not glossed over: the old GLib version's "runs on the
main/UI thread" guarantee doesn't hold here -- a threading.Timer fires on its own
worker thread, not any particular UI thread. Callers that touch Qt widgets from a
scheduled callback need to marshal back to the UI thread themselves (e.g. via a Qt
signal connected with Qt.QueuedConnection) once the Qt UI exists; nothing here
does that for you. Fine for today's actual callers (NotificationSource and
AsyncCall), which only touch plain Python data, not GUI widgets, yet.
"""

import sys
import threading
import traceback
from collections.abc import Callable
from typing import Any, TypeVar

from pythia.util.log import logger

ResultType = TypeVar("ResultType")


class AsyncCall(threading.Thread):
    def __init__(
        self,
        func: Callable[..., ResultType],
        callback: Callable[[ResultType, Exception], None] | None,
        *args: Any,
        callback_target: Any = None,
        **kwargs: Any,
    ):
        """Execute `function` in a new thread then schedule `callback` for
        execution at idle time. If 'callback_target' is a widget and it is destroyed
        in the meantime, the callback is cancelled.
        """
        self.callback_task = None
        self.stop_request = threading.Event()

        super().__init__(target=self.target, args=args, kwargs=kwargs)
        self.function = func
        if not callback:
            self.callback = lambda r, e: None
        else:
            self.callback = self._protect_callback(callback, callback_target)
        self.daemon = kwargs.pop("daemon", True)
        self.start()

    def _protect_callback(
        self, callback: Callable[[Any, Exception], None], callback_target: Any = None
    ) -> Callable[[Any, Exception], None]:
        """Wraps and hooks up an on-destroyed handler on the callback_target that
        removes the callback; this prevents an AsyncJob from completing on a
        destroyed widget, which can cause a crash.

        If no callback_target is given, this will use the receiver of the callback
        (if it has one)."""
        if not callback_target:
            callback_target = callback.__self__ if hasattr(callback, "__self__") else None

        if callback_target and hasattr(callback_target, "call_when_destroyed"):

            def unhook() -> None:
                # If the target is destroyed, block the callback; no need to disconnect
                # from a dead object.
                self.callback = lambda r, e: None

            def fire(r: Any, e: Exception) -> None:
                # Before starting the callback, unhook the on-destroyed callback
                # so we don't leak it.
                disconnecter()
                callback(r, e)

            disconnecter = callback_target.call_when_destroyed(unhook)
            return fire
        else:
            return callback

    def target(self, *a: Any, **kw: Any) -> None:
        result = None
        error = None

        try:
            result = self.function(*a, **kw)
        except Exception as ex:  # pylint: disable=broad-except
            logger.error("Error while completing task %s: %s %s", self.function, type(ex), ex)
            error = ex
            _ex_type, _ex_value, trace = sys.exc_info()
            traceback.print_tb(trace)

        self.callback_task = schedule_at_idle(self.callback, result, error)


class IdleTask:
    """This class provides a safe interface for cancelling idle tasks and timeouts;
    this will simply do nothing after being used once, and once the task completes,
    it will also do nothing.

    These objects are returned by the schedule methods below, which disconnect
    them when appropriate."""

    def __init__(self) -> None:
        """Initializes a task with no connection to a source, but also not completed; this can be
        connected to a source via the connect() method, unless it is completed first."""
        self._timer: threading.Timer | None = None
        self._is_completed = False

    def unschedule(self) -> None:
        """Call this to prevent the idle task from running, if it has not already run."""
        if self._timer is not None:
            self._timer.cancel()
            self.disconnect()

    def is_completed(self) -> bool:
        """True if the idle task has completed; that is, if mark_completed() was called on it."""
        return self._is_completed

    def connect(self, timer: threading.Timer) -> None:
        """Connects this task to a timer to be unscheduled; but if the task is already
        completed, this does nothing."""
        if not self._is_completed:
            self._timer = timer

    def disconnect(self) -> None:
        """Break the link to the idle task, so it can't be unscheduled."""
        self._timer = None

    def mark_completed(self) -> None:
        """Marks the task as completed, and also disconnect it."""
        self._is_completed = True
        self.disconnect()


# A task that is always completed and disconnected and does nothing.
COMPLETED_IDLE_TASK = IdleTask()
COMPLETED_IDLE_TASK.mark_completed()


def schedule_at_idle(func: Callable[..., None], *args: Any, delay_seconds: float = 0.0) -> IdleTask:
    """Schedules a function to run at idle time, once. You can specify a delay in seconds
    before it runs (a negative delay runs as soon as possible, same as zero here).
    Returns an object to prevent it running."""

    task = IdleTask()

    def wrapper() -> None:
        try:
            func(*args)
        finally:
            task.disconnect()

    timer = threading.Timer(max(delay_seconds, 0.0), wrapper)
    timer.daemon = True
    task.connect(timer)
    timer.start()
    return task


def run_until_complete(
    async_func: Callable[..., None], *args: Any, timeout_seconds: float = 30.0, **kwargs: Any
) -> None:
    """Blocks the calling thread until an async operation completes.

    Intended for CLI code paths with no GUI event loop running. The async_func
    receives two extra keyword arguments -- ``completion_function`` and
    ``error_function`` -- which it must call when the operation finishes.

    Raises whatever exception is passed to error_function, or TimeoutError
    if timeout_seconds elapses first.
    """
    done_event = threading.Event()
    error_holder: list[Exception | None] = [None]

    def on_complete() -> None:
        done_event.set()

    def on_error(ex: Exception) -> None:
        error_holder[0] = ex
        done_event.set()

    async_func(*args, completion_function=on_complete, error_function=on_error, **kwargs)

    if not done_event.wait(timeout_seconds):
        raise TimeoutError(f"Async operation timed out after {timeout_seconds}s")

    if error_holder[0]:
        raise error_holder[0]


def schedule_repeating_at_idle(
    func: Callable[..., bool],
    *args: Any,
    interval_seconds: float = 0.0,
) -> IdleTask:
    """Schedules a function to run at idle time, over and over until it returns False.
    It can be repeated at an interval in seconds, which will also delay it's first invocation.
    Returns an object to stop it running."""

    task = IdleTask()

    def _reschedule() -> None:
        timer = threading.Timer(max(interval_seconds, 0.0), wrapper)
        timer.daemon = True
        task.connect(timer)
        timer.start()

    def wrapper() -> None:
        repeat = False
        try:
            repeat = func(*args)
        finally:
            if repeat and not task.is_completed():
                _reschedule()
            else:
                task.disconnect()

    _reschedule()
    return task
