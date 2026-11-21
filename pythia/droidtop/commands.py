"""The droidtop commands as the running Pythia answers them (pythia.instance; docs/SPEC.md, decision 12).

`pythia --droidtop-serve` and the pairing commands are requests to the running app, which serves paired
handhelds for as long as it runs, window open or in the tray. Each handler takes the request's arguments and
`say`, which sends a line back to the command that asked, and returns the command's exit code.
"""

import time
from collections.abc import Callable

from pythia.droidtop import AgentError, pair, service, store

Say = Callable[[str], None]

# How long a shown pairing code waits for a handheld.
PAIR_WAIT = 600.0


def serve(_args: dict, say: Say) -> int:
    """Makes sure the running Pythia serves paired handhelds, and says so; it keeps serving after this."""
    me = store.identity()
    if service.serving().get("state") != "serving":
        service.serve()
    state = service.serving()
    if state.get("state") != "serving":
        say(state.get("reason", "The agent did not start."))
        return 1
    say(f"{me['name']} ({me['id'][:16]}) serves paired droidtop handhelds while Pythia runs (see its tray icon).")
    return 0


def pair_with(args: dict, say: Say) -> int:
    say(f"Paired with {pair.pair(args['code'])}.")
    return 0


def pair_show(_args: dict, say: Say) -> int:
    shown = pair.show()
    say("On droidtop: Settings > Computers > Pair a computer > Use a code from the computer.")
    for place in shown["places"]:
        say(f"Address: {place['address']}  ({place['label']})")
    say(f"Code:    {shown['code'][:3]} {shown['code'][3:]}")
    say("Waiting up to 10 minutes.")
    deadline = time.monotonic() + PAIR_WAIT
    while (status := pair.status())["state"] == "waiting":
        if time.monotonic() > deadline:
            pair.cancel()
            say("No handheld paired in time.")
            return 1
        time.sleep(0.5)
    if status["state"] != "paired":
        say(status.get("error", "Pairing stopped."))
        return 1
    say(f"Paired with {status['paired']}.")
    return 0


def devices(_args: dict, say: Say) -> int:
    found = store.devices()
    if not found:
        say("No handheld is paired. On droidtop: Settings > Computers > Pair a computer.")
    for d in found:
        say(f"{d['id'][:16]}  {d['name']}")
    return 0


def unpair(args: dict, say: Say) -> int:
    store.unpair(args["id"])
    say("Forgotten.")
    return 0


def _guarded(handler: Callable[[dict, Say], int]) -> Callable[[dict, Say], int]:
    def run(args: dict, say: Say) -> int:
        try:
            return handler(args, say)
        except AgentError as ex:
            say(str(ex))
            return 1

    return run


# The ops pythia.app's droidtop flags send, by name.
HANDLERS = {
    "droidtop-serve": _guarded(serve),
    "droidtop-pair": _guarded(pair_with),
    "droidtop-pair-show": _guarded(pair_show),
    "droidtop-devices": _guarded(devices),
    "droidtop-unpair": _guarded(unpair),
}
