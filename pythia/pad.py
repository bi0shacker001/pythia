"""Game controllers for the handheld shell: one input source, in the backend.

UX review 2026-10-16 (tracker coordination/pythia/ux-review-2026-10-16.md),
A7 (HH11) and docs/SPEC.md, "The pad model": droidtop's native module reads
every controller on Windows and Linux (``pythia._native.pad``, gilrs; Steam
Input's virtual pad as well as the raw device) and names what was pressed
(``a``, ``b``, ``x``, ``y``, ``l1``, ``r1``, ``l2``, ``r2``, ``start``,
``select``, and ``up``/``down``/``left``/``right``, repeating while held).
This drains those actions on the GUI thread and hands them to the page, but
only while one of Pythia's windows has the system's focus: a press meant for
another app, or for a file manager or launcher Pythia handed off to, is
dropped, never queued. The page does not also read the browser's Gamepad
API, so each press is seen once.
"""

from collections.abc import Callable, Iterable
from typing import Any

from pythia.util.log import logger

ACTIONS = frozenset({"a", "b", "x", "y", "l1", "r1", "l2", "r2", "start", "select", "up", "down", "left", "right"})
# How often the GUI thread drains the queue: about a frame.
POLL_MS = 16


def deliver(actions: Iterable[str], focused: bool, send: Callable[[str], None]) -> int:
    """Sends each known action while Pythia has the focus; drops them all
    otherwise. Returns how many were sent."""
    if not focused:
        return 0
    sent = 0
    for action in actions:
        if action in ACTIONS:
            send(action)
            sent += 1
    return sent


def pythia_has_focus() -> bool:
    """Whether one of Pythia's windows is the system's active window."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication

    return QGuiApplication.applicationState() == Qt.ApplicationState.ApplicationActive


def qt_timer() -> Any:
    from PySide6.QtCore import QTimer

    return QTimer()


class PadReader:
    """Starts the native reader and drains it on a Qt timer, calling ``send``
    with each action while Pythia has the focus."""

    def __init__(
        self,
        send: Callable[[str], None],
        focused: Callable[[], bool] = pythia_has_focus,
        make_timer: Callable[[], Any] = qt_timer,
    ):
        self._send = send
        self._focused = focused
        self._make_timer = make_timer
        self._timer: Any = None
        self._module: Any = None
        self.problem: str | None = None

    def start(self) -> bool:
        from pythia.droidtop import native

        try:
            module = native.submodule("pad")
            module.start()
        except (native.AgentError, AttributeError) as ex:
            self.problem = f"Controllers are not read: {ex}"
            logger.warning(self.problem)
            return False
        self._module = module
        self._timer = self._make_timer()
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self.drain)
        self._timer.start()
        return True

    def drain(self) -> None:
        if self._module is None:
            return
        deliver(self._module.poll(), self._focused(), self._send)

    def pads(self) -> list[dict]:
        return list(self._module.pads()) if self._module is not None else []
