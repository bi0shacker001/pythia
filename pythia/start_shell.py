"""Which shell the page starts in, and whether this computer is a handheld.

UX review 2026-10-16 (tracker coordination/pythia/ux-review-2026-10-16.md),
A8 (N2): one "Start in" setting, kept by Pythia (local.db, per computer, like
its other settings) rather than in the page's storage, so it survives a
restart and a cleared browser profile:

- ``auto``: Handheld on a handheld, Desktop otherwise;
- ``desktop`` or ``handheld``: always that one;
- ``last``: the shell of the last deliberate, confirmed switch (A4).
  Start-up, Back and visits never change it.

A handheld is found by the device, not the screen size: the maker and model
the firmware reports (DMI on Linux, the BIOS keys in the registry on
Windows), matched against known PC handhelds. A GPD Win 4 reports maker
"GPD"; a Steam Deck reports "Valve" and model "Jupiter" or "Galileo".
"""

import sys
from pathlib import Path

from pythia.identity import get_setting, set_setting

START_IN = ("auto", "desktop", "handheld", "last")
SHELLS = ("desktop", "handheld")

# Makers whose PCs are all handhelds, as their firmware spells them (compared case-insensitively).
HANDHELD_MAKERS = ("gpd", "ayaneo", "ayadevice", "ayn", "one-netbook", "onexplayer")
# Handheld models from makers that also make other PCs: (maker, model prefix).
HANDHELD_MODELS = (
    ("valve", "jupiter"),  # Steam Deck LCD
    ("valve", "galileo"),  # Steam Deck OLED
    ("asustek", "rog ally"),
    ("asustek", "rc71l"),  # ROG Ally
    ("asustek", "rc72la"),  # ROG Ally X
    ("lenovo", "83e1"),  # Legion Go
    ("lenovo", "83l3"),  # Legion Go S
    ("lenovo", "83n6"),  # Legion Go S
    ("lenovo", "83q2"),  # Legion Go S
    ("lenovo", "83q3"),  # Legion Go S
    ("micro-star", "claw"),  # MSI Claw
)

DMI = Path("/sys/class/dmi/id")


def _firmware_identity() -> tuple[str, str]:
    """(maker, model) as the firmware reports them, or empty strings."""
    if sys.platform == "win32":
        try:
            import winreg  # noqa: PLC0415

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\BIOS") as key:
                maker = winreg.QueryValueEx(key, "SystemManufacturer")[0]
                model = winreg.QueryValueEx(key, "SystemProductName")[0]
            return str(maker), str(model)
        except OSError:
            return "", ""

    def read(name: str) -> str:
        try:
            return (DMI / name).read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            return ""

    return read("sys_vendor"), read("product_name")


def handheld(maker: str, model: str) -> bool:
    maker, model = maker.casefold(), model.casefold()
    if any(maker.startswith(m) for m in HANDHELD_MAKERS):
        return True
    return any(maker.startswith(m) and model.startswith(p) for m, p in HANDHELD_MODELS)


def detect() -> dict:
    """``handheld`` (bool) and ``device`` (the maker and model, for Settings)."""
    maker, model = _firmware_identity()
    return {"handheld": handheld(maker, model), "device": " ".join(x for x in (maker, model) if x) or None}


def start_in() -> str:
    value = get_setting("start_in")
    return value if value in START_IN else "auto"


def set_start_in(value: str) -> None:
    if value not in START_IN:
        raise ValueError(f"Start in is one of {', '.join(START_IN)}, not {value!r}")
    set_setting("start_in", value)


def remember(shell: str) -> None:
    """A confirmed, deliberate switch to ``shell`` (A4): what "last" starts in."""
    if shell not in SHELLS:
        raise ValueError(f"not a shell: {shell!r}")
    set_setting("last_shell", shell)


def initial_shell(device: dict | None = None) -> str:
    """The shell to start in now."""
    choice = start_in()
    if choice in SHELLS:
        return choice
    if choice == "last":
        last = get_setting("last_shell")
        return last if last in SHELLS else "desktop"
    device = device if device is not None else detect()
    return "handheld" if device["handheld"] else "desktop"
