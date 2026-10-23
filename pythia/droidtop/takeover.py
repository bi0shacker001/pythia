"""droidtop-agent, the separate program, while Pythia serves the handhelds
(``pythia._native.agent``).

Owner, 2026-10-16: the two cannot serve one computer together, so Pythia
turns the agent off when it serves (stops it, and turns off its start at
sign-in, keeping that entry to put back); and their state syncs both ways:
Pythia always takes in what the agent has newer, and writes back into the
agent's folder while the agent is not running, so turning it back on finds
the same pairings, library and save copies.

"Sync with droidtop" is Pythia's setting ``droidtop_serve`` (see serves()
for its first value; docs/SPEC.md 1.7). While it is on Pythia listens on
the network, announces this computer and takes droidtop-agent's place;
while it is off Pythia does none of that and never writes droidtop-agent's
folder.
"""

import threading

from pythia import settings
from pythia.droidtop.native import AgentError, available, submodule
from pythia.util.log import logger

SERVE_SETTING = "droidtop_serve"


def serves() -> bool:
    """Whether Pythia serves the handhelds itself: Sync with droidtop.

    Pythia is a game library manager first and is not necessarily used with
    droidtop (owner, 2026-10-16). Until the person sets it, the switch is on
    only where droidtop is already in use: droidtop-agent has its files here,
    runs, or starts at sign-in (docs/SPEC.md 1.7). That first answer is kept
    as the setting, so Pythia's own later writes into the agent's folder do
    not change it."""
    text = settings.read_setting(SERVE_SETTING, "").casefold()
    if text in ("true", "false"):
        return text == "true"
    on = droidtop_in_use()
    settings.write_setting(SERVE_SETTING, on)
    return on


def droidtop_in_use() -> bool:
    """droidtop-agent is on this computer: it has its files, runs, or starts
    at sign-in. Looks only; never writes the agent's folder."""
    agent = submodule("agent")
    if agent.folders().get("exists"):
        return True
    st = agent.status()
    return bool(st["running"] or st["starts_at_sign_in"] or st["turned_off"])


def status() -> dict:
    """``running`` (the agent's processes: ``pid``, ``name``),
    ``starts_at_sign_in``, ``turned_off`` (None, or ``since`` and the
    ``entries`` Pythia keeps), ``folders``, and ``pythia_serves``."""
    result = dict(submodule("agent").status())
    result["pythia_serves"] = serves()
    return result


def sync(write_back: bool) -> dict:
    """Takes in the agent's newer state; with ``write_back``, writes Pythia's
    newer state into the agent's folder (see pythia._native.agent.sync)."""
    return submodule("agent").sync(write_back=write_back)


def take_over() -> dict:
    """What Pythia does before it serves: turns a running or self-starting
    agent off, then syncs, writing back unless the agent is still running.
    Returns ``turned_off`` (the start entries removed), ``still_running``,
    and the ``sync`` summary. Raises AgentError when the agent could not be
    turned off; the agent then keeps serving."""
    agent = submodule("agent")
    before = agent.status()
    result: dict = {"turned_off": [], "still_running": []}
    if before["running"] or before["starts_at_sign_in"]:
        done = agent.turn_off(wait=10.0)
        result["turned_off"] = done["entries"]
        result["still_running"] = done["still_running"]
    result["sync"] = agent.sync(write_back=not result["still_running"])
    return result


def turn_agent_back_on() -> dict:
    """Turns Sync with droidtop off. When Pythia had taken droidtop-agent's
    place, puts the agent's start at sign-in back as it was, with Pythia's
    state written into its folder first; otherwise the agent's folder is not
    touched. This Pythia keeps serving until it quits."""
    agent = submodule("agent")
    settings.write_setting(SERVE_SETTING, False)
    if not agent.status().get("turned_off"):
        return {"entries": [], "sync": None}
    synced = agent.sync(write_back=True)
    done = agent.turn_on()
    return {"entries": done["entries"], "sync": synced}


def serve_from_pythia_again() -> None:
    """Sync with droidtop on: the person's consent (docs/SPEC.md 1.7). The
    caller starts serving now (pythia.droidtop.service.start_in_background)."""
    settings.write_setting(SERVE_SETTING, True)


_sync_lock = threading.Lock()
_sync_wanted = threading.Event()
_sync_thread: threading.Thread | None = None


def sync_soon() -> None:
    """sync() on a background thread, writing back while Pythia serves and
    the agent does not run: what a front end calls after the handhelds'
    state changed (a pairing, a session). Changes during a sync get one more."""
    global _sync_thread
    if not available():
        return
    with _sync_lock:
        _sync_wanted.set()
        if _sync_thread is not None and _sync_thread.is_alive():
            return

        def run():
            global _sync_thread
            while True:
                with _sync_lock:
                    if not _sync_wanted.is_set():
                        _sync_thread = None
                        return
                    _sync_wanted.clear()
                try:
                    st = status()
                    sync(write_back=st["pythia_serves"] and not st["running"])
                except AgentError as ex:
                    logger.warning("Syncing with droidtop-agent's folder failed: %s", ex)
                except Exception as ex:  # a background thread: log, never raise
                    logger.exception("Syncing with droidtop-agent's folder failed: %s", ex)

        _sync_thread = threading.Thread(target=run, name="droidtop-agent-sync", daemon=True)
        _sync_thread.start()
