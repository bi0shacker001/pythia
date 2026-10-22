"""Pythia's two modes, and the rule between them.

- **Agent mode:** Pythia serves droidtop handhelds as this computer's droidtop
  agent (owner, 2026-10-14: "If it's running, it will BE our agent"): its
  droidtop module is built and serving is on (``pythia.droidtop.takeover``,
  setting ``droidtop_serve``).
- **Manager mode:** the library and version manager of Droidtop/tracker#214
  (owner, 2026-10-01: "We want a library and version manager [...] we should
  be able to tag game folders, keep a local database like with f95checker
  [...] a user can tap a link, tag a download with the game it relates to and
  its type"). Setting ``manager_mode``, on by default.

Owner, 2026-10-16: "For pythia, agent mode should force manager mode." While
Pythia is the agent, manager mode is on and cannot be turned off: the agent
syncs the library that manager mode keeps. The UI shows the switch locked,
with ``reason``, rather than hiding it.

Awaiting the owner's confirmation: that "manager mode" is #214's manager, and
what turning it off pauses. Until then it pauses ``MANAGER_PAGES`` (the
Downloads & intake page: watching folders and tagging what arrives), which the
UI marks off with a way back on. Change that list, not the pages, to adjust.
"""

from pythia import settings

MANAGER_SETTING = "manager_mode"
# The pages manager mode turning off pauses (see the docstring).
MANAGER_PAGES = ("downloads",)
LOCKED_REASON = (
    "Sync with droidtop is on, and it syncs the library that manager mode keeps with your handhelds, "
    "so manager mode stays on while it is."
)
# What "Sync with droidtop" is, for its Info row (UX review A9: no label says "agent"; Info does).
SYNC_INFO = (
    "Sync with droidtop is droidtop's agent mode: Pythia serves your paired droidtop handhelds as this "
    "computer's droidtop agent, syncing the library, saves and launches with them, and turns the separate "
    "droidtop-agent program off so the two never serve at once. While it is on, manager mode stays on, "
    "because what syncs is the library manager mode keeps. It starts on only where droidtop-agent is already "
    "on this computer. Turning it off puts droidtop-agent's start at sign-in back as it was, with Pythia's state "
    "written into its folder; Pythia stops serving when it quits. While it is off Pythia does not use the network "
    "for handhelds or touch droidtop-agent's folder."
)


class ModeLocked(Exception):
    """Manager mode cannot be turned off while Pythia is the agent."""


def agent_mode() -> bool:
    """Whether Pythia is the droidtop agent: its module is built, and it
    serves handhelds now or will from its next start (droidtop_serve). After
    "Turn droidtop-agent back on" it still serves until it quits."""
    from pythia import droidtop  # noqa: PLC0415 (droidtop loads the native module)

    if not droidtop.available():
        return False
    from pythia.droidtop import service, takeover  # noqa: PLC0415

    if takeover.serves():
        return True
    try:
        return service.serving().get("state") == "serving"
    except droidtop.AgentError:
        return False


def sync_with_droidtop() -> dict:
    """ "Sync with droidtop" (agent mode): ``on`` (Pythia serves from its
    start, setting droidtop_serve), ``serving`` (it serves now), and
    ``available`` with the ``reason`` when its droidtop module is missing."""
    from pythia import droidtop  # noqa: PLC0415

    if not droidtop.available():
        return {
            "on": False,
            "serving": False,
            "available": False,
            "reason": droidtop.why_unavailable(),
            "info": SYNC_INFO,
        }
    from pythia.droidtop import takeover  # noqa: PLC0415

    return {"on": takeover.serves(), "serving": agent_mode(), "available": True, "reason": None, "info": SYNC_INFO}


def manager_mode() -> dict:
    """``on``, ``locked`` (agent mode forces it on), the ``reason`` when
    locked, the ``pages`` it covers, and ``agent`` (agent mode)."""
    agent = agent_mode()
    chosen = settings.read_bool_setting(MANAGER_SETTING, True)
    return {
        "on": True if agent else chosen,
        "locked": agent,
        "reason": LOCKED_REASON if agent else None,
        "pages": list(MANAGER_PAGES),
        "agent": agent,
    }


def set_manager_mode(on: bool) -> dict:
    """Turns manager mode on or off; turning it off in agent mode raises
    ModeLocked with the reason, and nothing changes."""
    if not on and agent_mode():
        raise ModeLocked(LOCKED_REASON)
    settings.write_setting(MANAGER_SETTING, bool(on))
    return manager_mode()


SERVICE_STATES = {
    "enabled": "On",
    "disabled": "Off (the helper is installed)",
    "absent": "Off",
    "not_available": "Only on Windows",
}


def service_mode() -> dict:
    """Service mode (docs/SPEC.md 1.10): ``on``, the ``state`` in words
    (``state_key`` as pythia.secure_screen says it), ``available`` with the
    ``reason`` when it is not, ``installed`` (the helper is there), and the
    ``consent`` shown before it is turned on."""
    from pythia import secure_screen  # noqa: PLC0415

    key = secure_screen.state()
    return {
        "on": key == "enabled",
        "state": SERVICE_STATES.get(key, key),
        "state_key": key,
        "available": key != "not_available",
        "reason": None if key != "not_available" else "Service mode is only on Windows.",
        "installed": key in ("enabled", "disabled"),
        "consent": secure_screen.consent(),
    }
