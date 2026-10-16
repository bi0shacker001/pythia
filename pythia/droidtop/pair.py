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
