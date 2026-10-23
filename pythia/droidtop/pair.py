"""Pairing a handheld from this computer (``pythia._native.pair``), the way
droidtop-agent's window does it. Refusals raise AgentError."""

from pythia.droidtop.native import submodule


def pair(code: str) -> str:
    """Pairs with a handheld that shows ``code`` (droidtop: Settings >
    Computers > Pair a computer): the 6 digits or its QR code's text. Waits
    for the exchange (seconds): never call it from the UI thread. Returns
    the handheld's name."""
    return submodule("pair").pair(code)


def show() -> dict:
    """This computer shows a code for 10 minutes (droidtop: Pair a computer >
    Use a code from the computer): ``code``, ``places`` (each address with
    its ``label``), ``invite`` (a QR code's text) and ``seconds`` left. Ask
    ``status()`` for the outcome."""
    return submodule("pair").show()


def status() -> dict:
    """``state``: ``idle``, ``waiting``, ``paired`` (``paired``: the name) or
    ``failed`` (``error``)."""
    return submodule("pair").status()


def cancel() -> None:
    submodule("pair").cancel()


def places() -> list[dict]:
    """Every address a handheld can reach this computer at, labelled."""
    return submodule("pair").places()


# ---- One code on both screens (docs/SPEC.md 7.1, 7.2) -------------------------


def offer() -> dict:
    """This computer can be picked (by droidtop's Pair a computer, or by
    another Pythia) until ``stop()``: it answers on the local network only
    while this runs. Returns this computer's ``name`` and pairing ``port``."""
    return submodule("pair").offer()


def find(seconds: float = 3.0) -> list[dict]:
    """Other computers offering to pair on this network: ``id``, ``name``,
    ``program`` (``pythia`` or ``droidtop-agent``) and ``address``. Waits
    ``seconds``: never call it from the UI thread."""
    return submodule("pair").find(seconds=seconds)


def pick(computer: dict) -> dict:
    """Starts comparing with a computer ``find()`` listed: returns the
    ``code`` both screens show, and its ``name``."""
    return submodule("pair").pick(computer["id"], computer["name"], computer.get("program", ""), computer["address"])


def compare_status() -> dict:
    """``state``: ``idle``, ``offering`` (``seconds`` waited), ``comparing``
    (``code``, ``name``), ``finishing``, ``paired`` (``paired``: the name) or
    ``failed`` (``error``)."""
    return submodule("pair").compare_status()


def confirm(same: bool) -> str:
    """The person's answer to "Do both screens show this code?". Waits for
    the other device's answer (up to two minutes): never call it from the UI
    thread. Returns the name paired with; AgentError when nothing was."""
    return submodule("pair").confirm(same)


def stop() -> None:
    """Stops offering and any comparison under way."""
    submodule("pair").stop()
