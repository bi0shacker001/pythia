"""Service mode: windowcast's secure-screen helper (docs/SPEC.md 1.10).

The helper runs as SYSTEM so a paired device can see and answer this
computer's Windows security screens (User Account Control prompts, the lock
and sign-in screen). Owner, on windowcast#467: "We won't just run code as
SYSTEM without user approval, we'll ask, and let them enable/disable in
settings at their own leisure." So it is off until the person turns it on in
Settings, after reading ``consent()``, and every change asks Windows for
administrator approval: Pythia runs this module again elevated
(``python -I -m pythia.secure_screen <action> --confirmed ...``), which does
only that change through windowcast's client API, by way of
``pythia._native.secure_screen`` (droidtop-agent's ``secure_screen``).

This module imports only ``pythia._native``, never Pythia's settings or
data, so the elevated copy writes nothing in an administrator's profile.
"""

import sys
from pathlib import Path

try:  # built with Pythia; missing only in a source tree that was never built
    from pythia import _native  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover - depends on the build
    _native = None

# The helper Pythia's Windows wheel carries beside this module.
HELPER = Path(__file__).with_name("windowcast-secure-helper.exe")


def state() -> str:
    """``not_available`` (not Windows, or no native module), ``absent``,
    ``disabled`` or ``enabled``."""
    if _native is None:
        return "not_available"
    return _native.secure_screen.state()


def consent() -> str:
    """What service mode does, said before it is turned on."""
    if _native is None:
        return ""
    return _native.secure_screen.consent()


def change(action: str) -> str:
    """``enable``, ``disable`` or ``uninstall``, with Windows' administrator
    approval; waits for the person. Returns what happened in words; raises
    ``pythia._native.AgentError`` with the reason when it did not happen."""
    if _native is None:
        raise RuntimeError("Pythia's native module is not built")
    return _native.secure_screen.request(action, sys.executable, ["-I", "-m", "pythia.secure_screen"])


def main(argv: list[str]) -> int:
    """The elevated copy: ``<action> --confirmed [--grant-sid <sid>]``."""
    if _native is None or len(argv) < 2 or argv[1] != "--confirmed":
        return 3
    sid = None
    if argv[2:]:
        if len(argv) != 4 or argv[2] != "--grant-sid":
            return 3
        sid = argv[3]
    helper = str(HELPER) if HELPER.is_file() else None
    return int(_native.secure_screen.perform(argv[0], grant_sid=sid, helper=helper))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
