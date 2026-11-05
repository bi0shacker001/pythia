"""This computer as a KDE Connect device (Droidtop/tracker#645), through ``pythia._native.kdeconnect``.

The device is Droidtop/lib-kdeconnect's, the same one droidtop runs on a handheld: phones and computers with the
official KDE Connect apps, and droidtop handhelds, find this computer, pair with it (both screens show the same
8-character code), and share files, links, text and the clipboard with it. It runs while the person has it on
(``kdeconnect_on``, off until turned on) and Pythia runs. Files others share land in ``~/Downloads``. Its state
(the KDE Connect identity and the paired devices) is in Pythia's droidtop folder, ``DATA_DIR/droidtop/kdeconnect``.

Owner, 2026-11-05: "We want to get kde connect and tailscale integrated more closely. File transfer, keyboard
input, and etc are good first things".
"""

import os
import socket
import threading
from collections.abc import Callable
from typing import Any

from pythia import settings
from pythia.droidtop.native import FOLDER, AgentError, available, submodule, why_unavailable
from pythia.util.log import logger

ON_SETTING = "kdeconnect_on"
STATE_DIR = os.path.join(FOLDER, "kdeconnect")
EVENT_WAIT_MS = 30_000

_lock = threading.Lock()
_follower: threading.Thread | None = None
_handlers: list[Callable[[dict[str, Any]], None]] = []


def call(op: str, **args: Any) -> dict[str, Any]:
    """Runs lib-kdeconnect operation ``op`` (its ``crates/service/src/ops.rs``). Raises AgentError."""
    return submodule("kdeconnect").call(op, **args)


def is_on() -> bool:
    return settings.read_setting(ON_SETTING, "").casefold() in ("true", "1", "yes")


def set_on(on: bool) -> None:
    settings.write_setting(ON_SETTING, on)
    if on:
        start()
    else:
        stop()


def receive_dir() -> str:
    """Where files paired devices share are stored: the person's Downloads folder."""
    return os.path.join(os.path.expanduser("~"), "Downloads")


def on_event(handler: Callable[[dict[str, Any]], None]) -> None:
    """Calls ``handler`` with every event (``{"event": "pair_request", ...}``), on the follower's thread."""
    _handlers.append(handler)


def start() -> dict[str, Any]:
    """Starts the device and the thread that follows its events; returns ``{id, name}``. Raises AgentError, for
    example when the official KDE Connect app already runs on this computer (it holds UDP port 1716)."""
    global _follower
    with _lock:
        reply = call("start", state_dir=STATE_DIR, receive_dir=receive_dir(), name=socket.gethostname(), type="desktop")
        if _follower is None or not _follower.is_alive():
            _follower = threading.Thread(target=_follow, name="kdeconnect-events", daemon=True)
            _follower.start()
        return reply


def stop() -> None:
    with _lock:
        call("stop")


def _follow() -> None:
    while True:
        try:
            event = call("event", timeout_ms=EVENT_WAIT_MS)
        except AgentError:
            return  # not on any more
        kind = event.get("event")
        if kind == "stopped":
            return
        if kind == "none":
            continue
        logger.debug("KDE Connect: %s", event)
        for handler in _handlers:
            try:
                handler(event)
            except Exception:  # noqa: BLE001 - one handler's failure must not stop the others
                logger.exception("A KDE Connect event handler failed")


def devices() -> list[dict[str, Any]]:
    """Every device seen this run and every paired one: ``id``, ``name``, ``type``, ``reachable``, ``paired``,
    ``pairing`` and, while a request is open, ``verification_key``."""
    return call("devices")["devices"]


def start_in_background() -> None:
    """Starts KDE Connect when the person left it on and Pythia's droidtop module is built: what a front end calls
    once at start. Never raises; logs why it does not run."""
    if not is_on():
        return
    if not available():
        logger.info("Not starting KDE Connect: %s", why_unavailable())
        return
    try:
        start()
    except AgentError as ex:
        logger.warning("KDE Connect did not start: %s", ex)
