"""This computer's own Tailscale node (Droidtop/tracker#645), through ``pythia._native.tailscale``.

lib-net's ``net-tailscale`` asks tailscaled's LocalAPI for its status, the way ktailctl and the ``tailscale`` CLI do:
no account token and no control server. Owner, 2026-11-05: "Tailscale applications do it too. Look at the tailscale
code, ktailctl ... We're building a local tailscale control plugin". A tailnet is used when there is one and never
required: without tailscaled, ``status()`` says ``{"present": False}``.

``follow_into_kdeconnect`` gives KDE Connect the tailnet addresses of the online peers, so a phone on the tailnet
away from home is reachable as on the same network.
"""

import threading
from typing import Any

from pythia.droidtop.native import AgentError, available, submodule
from pythia.util.log import logger

POLL_SECONDS = 30.0

_follower: threading.Thread | None = None
_again = threading.Event()  # set when KDE Connect (re)starts: it has to be told the addresses again


def status() -> dict[str, Any]:
    """``{"present": False}``, or ``{"present": True, "running", "state", "me", "peers"}`` with each peer's
    ``host_name``, ``dns_name``, ``os``, ``addresses`` (IPv4 first) and ``online``. Raises AgentError."""
    return submodule("tailscale").status()


def online_addresses(reply: dict[str, Any]) -> list[str]:
    """Every address of the online peers, when the tailnet is up; else none."""
    if not reply.get("present") or not reply.get("running"):
        return []
    return [a for p in reply.get("peers", []) if p.get("online") for a in p.get("addresses", [])]


def _follow() -> None:
    from pythia.droidtop import kdeconnect

    last: list[str] | None = None
    while True:
        if _again.is_set():
            _again.clear()
            last = None
        try:
            addresses = online_addresses(status())
        except AgentError as ex:
            logger.debug("Tailscale status: %s", ex)
            addresses = []
        if addresses != last:
            try:
                kdeconnect.call("addresses", addresses=addresses)
                last = addresses
            except AgentError:
                last = None  # KDE Connect is off; tell it again when it is on
        _again.wait(POLL_SECONDS)


def follow_into_kdeconnect() -> None:
    """Starts the thread that keeps KDE Connect's tailnet addresses current, or has it tell a KDE Connect that just
    started again. Never raises."""
    global _follower
    if not available():
        return
    _again.set()
    if _follower is not None and _follower.is_alive():
        return
    _follower = threading.Thread(target=_follow, name="tailscale-peers", daemon=True)
    _follower.start()
