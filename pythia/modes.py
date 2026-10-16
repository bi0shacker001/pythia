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
    "Pythia is this computer's droidtop agent, and the agent syncs the library that manager mode keeps, "
    "so manager mode stays on while Pythia serves your handhelds."
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
