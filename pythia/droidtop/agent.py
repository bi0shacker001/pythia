"""The droidtop agent inside Pythia: droidtop-agent's `droidtop_agent`
extension module, called with JSON (see this package's docstring).

The module is an optional dependency: a wheel from droidtop-agent's releases
(`pip install droidtop_agent-*.whl`). Without it Pythia works as before and
`available()` says why the handheld features are off.
"""

import json
import threading
from typing import Any

from pythia.util.log import logger

try:  # an optional, compiled dependency
    import droidtop_agent as _module  # type: ignore[import-not-found]

    _missing: str | None = None
except ImportError as ex:  # pragma: no cover - depends on the install
    _module = None
    _missing = (
        "droidtop's agent module is not installed (%s). Install the droidtop_agent wheel for this "
        "system from https://github.com/Droidtop/droidtop-agent/releases" % ex
    )


class AgentError(Exception):
    """A call the agent refused, in words a screen can show."""


def available() -> bool:
    return _module is not None


def why_unavailable() -> str | None:
    return _missing


def call(op: str, **args: Any) -> dict:
    """One call to the agent: `op` with `args` as JSON, the reply as a dict.
    Raises AgentError with the agent's own words when it refuses."""
    if _module is None:
        raise AgentError(_missing or "droidtop's agent module is not installed")
    reply = json.loads(_module.call(op, json.dumps(args)))
    if isinstance(reply, dict) and "error" in reply:
        raise AgentError(reply["error"])
    return reply


def identity() -> dict:
    """This computer's name and id as paired handhelds know it."""
    return call("identity")


def devices() -> list[dict]:
    return call("devices")["devices"]


def pair(code: str) -> str:
    """Pairs with a handheld showing `code` (droidtop: Settings > Computers >
    Pair a computer); returns its name. Waits for the exchange."""
    return call("pair", code=code)["paired"]


def pair_show() -> dict:
    """This computer shows a code for a handheld to type: the code, the
    addresses to use, and the invitation text a QR code carries. Poll
    `pair_status()` for the outcome."""
    return call("pair_show")


def pair_status() -> dict:
    return call("pair_status")


def pair_cancel() -> None:
    call("pair_cancel")


def unpair(device_id: str) -> None:
    call("unpair", id=device_id)


_serving_lock = threading.Lock()


def serve() -> dict:
    """Starts serving paired handhelds (LAN, discovery, WireGuard, the
    rendezvous, the cloud share) on the agent's own threads, after handing
    it Pythia's library. Fails with AgentError when another agent
    (droidtop-agent, or another Pythia) already serves this computer: that
    one keeps serving, over the same files."""
    with _serving_lock:
        from pythia.droidtop import library  # noqa: PLC0415 (it imports this module)

        library.export()
        return call("serve")


def serving() -> dict:
    return call("serving")


def start_in_background() -> None:
    """Serves in the background if the agent module is installed: what a
    front end calls once at start. Never raises; logs why it does not serve."""
    if not available():
        logger.info("Not serving droidtop handhelds: %s", why_unavailable())
        return

    def run():
        try:
            serve()
            logger.info("Serving droidtop handhelds as %s", identity().get("name"))
        except AgentError as ex:
            logger.warning("Not serving droidtop handhelds: %s", ex)
        except Exception as ex:  # keep a front end alive whatever the agent does
            logger.exception("The droidtop agent failed to start: %s", ex)

    threading.Thread(target=run, name="droidtop-agent", daemon=True).start()
