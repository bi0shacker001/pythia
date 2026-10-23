"""What other devices offer this computer (``pythia._native.remote``;
droidtop-agent docs/DESIGN.md section 20): browse a paired device's offered
games, copy one here, or ask that device to play one. The device decides
what it answers. Each call reaches the device over the network, so never
call one from the UI thread. Refusals raise AgentError."""

from pathlib import Path

from pythia.droidtop.native import submodule


def offered(device_id: str) -> dict:
    """What the paired device offers this computer: ``name``, ``offered``
    (``library``, ``saves``, ``launch``) and ``games`` (``key``, ``title``,
    ``platform``, ``version``, ``size``, ``marks``, ``accepted``)."""
    return submodule("remote").offered(device_id)


def accept(device_id: str, key: str, title: str, progress: Path | None = None) -> dict:
    """Copies the offered game into this computer's first game folder as a
    new folder; ``progress`` gets ``{"done", "total"}`` as it goes. Returns
    what was accepted (``folder``, ``version``...)."""
    return submodule("remote").accept(device_id, key, title, progress=str(progress) if progress else None)


def play_on(device_id: str, key: str, title: str) -> dict:
    """Asks the device to start the game there and stream it: ``result``
    (``started``, with ``stream``; ``not_allowed``, ``unknown_game``,
    ``steam_not_running``, ``failed`` with ``message``)."""
    return submodule("remote").play_on(device_id, key, title)


def accepted() -> list[dict]:
    """The games this computer accepted from other devices, newest first."""
    return submodule("remote").accepted()


# What play_on's answers mean, in the page's words.
PLAY_SAID = {
    "started": "Started there.",
    "not_allowed": "That device does not let this computer start games there. It can allow it on its own Devices page.",
    "unknown_game": "That device does not have that game installed.",
    "steam_not_running": "Steam is not running on that device.",
}


def play_said(outcome: dict) -> tuple[bool, str]:
    """Whether the game started, and what to say."""
    result = outcome.get("result") or ""
    if result == "started":
        stream = outcome.get("stream")
        return True, f"Started there; it streams through {stream}." if stream else "Started there."
    if result == "failed":
        return False, f"It could not start there: {outcome.get('message') or 'no reason given'}."
    return False, PLAY_SAID.get(result, f"That device answered {result}.")
