"""Serving paired handhelds while Pythia runs (``droidtop_agent.service``):
the same service as ``droidtop-agent run``, on the agent's own threads."""

import threading

from pythia.droidtop.native import AgentError, available, submodule, why_unavailable
from pythia.util.log import logger

_serving_lock = threading.Lock()


def serve() -> dict:
    """Hands the agent Pythia's library, then serves paired handhelds (LAN,
    discovery, WireGuard, the rendezvous, the cloud share). Raises AgentError
    when another agent (droidtop-agent, or another Pythia) already serves
    this computer: that one keeps serving, over the same files."""
    from pythia.droidtop import library  # noqa: PLC0415 (library imports the agent modules)

    with _serving_lock:
        library.export()
        return submodule("service").serve()


def serving() -> dict:
    """``state``: ``off``, ``serving``, or ``stopped`` with the ``reason``."""
    return submodule("service").serving()


def start_in_background() -> None:
    """Serves in the background if the agent module is installed: what a
    front end calls once at start. Never raises; logs why it does not serve."""
    if not available():
        logger.info("Not serving droidtop handhelds: %s", why_unavailable())
        return

    def run():
        try:
            serve()
            from pythia.droidtop import store  # noqa: PLC0415

            logger.info("Serving droidtop handhelds as %s", store.identity().get("name"))
        except AgentError as ex:
            logger.warning("Not serving droidtop handhelds: %s", ex)
        except Exception as ex:  # keep a front end alive whatever the agent does
            logger.exception("The droidtop agent failed to start: %s", ex)

    threading.Thread(target=run, name="droidtop-agent", daemon=True).start()
