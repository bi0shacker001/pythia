"""Starting one of Pythia's games for a paired handheld.

Owner, 2026-10-16: the agent launching only for Steam Link is the Steam
case; "given windowcast, yeah, have it be able to do general launches". So
Pythia registers a launcher with the agent's service
(``pythia._native.service.set_launcher``): for a handheld the person let
start games here ("Let it start games here" on Devices), every key that is not
Steam's comes here. Steam keys stay the agent's (Steam Link).

This is an exception to Pythia's "never launches anything" boundary, made on
purpose and recorded with it (``pythia.plugins``, 2026-10-16). A key is only
looked up, never run: it names one of this computer's tracked games
(``library.game_key``, the key Pythia hands the agent). The game then starts,
first way that applies:
- a variant with a runner set up (Lutris's runners: linux, wine...) starts
  as ``pythia pythia:rungameid/<game id>`` would, in a Pythia process of its
  own, so the window process never carries a game;
- else its store's launcher, through a manager plugin's hand-off
  (``pythia.plugins.manager``: Epic's, for instance);
- else the handheld is told why it cannot start.
windowcast streams the window that opens.
"""

import subprocess
import sys

from pythia import datadir
from pythia.droidtop.library import game_key
from pythia.droidtop.native import submodule
from pythia.identity import get_system_uuid
from pythia.util.log import logger

STREAM = "windowcast"


def launch_for_handheld(key: str, title: str = "") -> dict | None:
    """The agent's launcher: None when ``key`` is not one of this computer's
    tracked games, else the outcome as the protocol has it."""
    from pythia.plugins import manager as manager_plugins  # noqa: PLC0415 (the plugins import the UI-less core)
    from pythia.plugins.context import PluginContext  # noqa: PLC0415

    if key.startswith("steam:"):
        return None
    uuid = get_system_uuid()
    entries = [e for e in datadir.list_all_entries() if e.get("system_uuid") == uuid and game_key(e) == key]
    if not entries:
        return None
    name = title or entries[0].get("canonical_name") or key
    with_runner = next((e for e in entries if any(v.get("runner") for v in _variants(e))), None)
    if with_runner is not None:
        return _run_in_pythia(with_runner["game_id"], name)
    ctx = PluginContext(log=lambda message: logger.info("%s", message))
    usable = [(p, d) for p, d in manager_plugins.find_for_game(entries) if p.is_available(ctx)]
    if not usable:
        return {
            "result": "failed",
            "message": f"{name} cannot start here yet: set up how it runs in Pythia (a runner), "
            "or install it with its store's launcher",
        }
    plugin, detection = usable[0]
    logger.info("Starting %s for a handheld through %s", name, plugin.display_name)
    if not plugin.open(detection, ctx):
        return {"result": "failed", "message": f"{plugin.display_name} did not start {name}"}
    return {"result": "started", "stream": STREAM}


def _variants(entry: dict):
    for group in entry.get("versions") or []:
        yield from group.get("variants") or []


def _run_in_pythia(game_id: str, name: str) -> dict:
    """Starts the game as ``pythia pythia:rungameid/<game_id>``, in a process
    of its own that runs until the game stops, once Lutris's own check says
    it can (installed, with its runner and configuration); else the handheld
    gets that check's words."""
    from pythia.exceptions import GameConfigError  # noqa: PLC0415
    from pythia.game import Game  # noqa: PLC0415

    try:
        Game(game_id).check_launchable()
    except GameConfigError as ex:
        return {"result": "failed", "message": f"{name} cannot start here: {ex}"}
    command = [sys.executable, "-m", "pythia.launch", f"pythia:rungameid/{game_id}"]
    try:
        subprocess.Popen(command, stdin=subprocess.DEVNULL, start_new_session=True)  # noqa: S603 (no handheld text in it)
    except OSError as ex:
        return {"result": "failed", "message": f"Pythia could not start {name}: {ex}"}
    logger.info("Starting %s for a handheld (%s)", name, " ".join(command))
    return {"result": "started", "stream": STREAM}


def supported() -> bool:
    """Whether this build can start Pythia's own games for a handheld."""
    return hasattr(submodule("service"), "set_launcher")


def register() -> bool:
    """Hands launch_for_handheld to the agent's service; False (and Steam
    games only, as before) when this build has no host launches yet
    (native/'s "host-launch" feature, which needs droidtop-agent's
    launch::Launcher)."""
    if not supported():
        logger.info("Handhelds can start only Steam games here: this build cannot start Pythia's own for them yet")
        return False
    submodule("service").set_launcher(launcher=launch_for_handheld)
    return True
