"""Loading droidtop-agent's ``droidtop_agent`` extension module.

It is an optional, compiled dependency: a wheel from droidtop-agent's
releases (``pip install droidtop_agent-*.whl``). Its refusals raise
``droidtop_agent.AgentError``, which is this package's ``AgentError``; without
the module, ``submodule()`` raises an ``AgentError`` of this package's own
saying why.
"""

from types import ModuleType

RELEASES = "https://github.com/Droidtop/droidtop-agent/releases"

try:  # an optional, compiled dependency
    import droidtop_agent as _module  # type: ignore[import-not-found]

    _missing: str | None = None
except ImportError as ex:  # pragma: no cover - depends on the install
    _module = None
    _missing = (
        f"droidtop's agent module is not installed ({ex}). Install the droidtop_agent wheel for this "
        f"system from {RELEASES}"
    )

# A wheel from before the agent's crates had their own submodules has one JSON
# call and no AgentError: Pythia needs a newer one.
if _module is not None and not hasattr(_module, "AgentError"):  # pragma: no cover - depends on the install
    _missing = f"the installed droidtop_agent module is too old for Pythia. Install the newest wheel from {RELEASES}"
    _module = None

if _module is not None:
    AgentError = _module.AgentError
else:

    class AgentError(Exception):  # type: ignore[no-redef]
        """A call the agent refused, in words a screen can show."""


def available() -> bool:
    return _module is not None


def why_unavailable() -> str | None:
    return _missing


def submodule(name: str) -> ModuleType:
    """``droidtop_agent.<name>``, or AgentError saying why it is missing."""
    if _module is None:
        raise AgentError(_missing or "droidtop's agent module is not installed")
    return getattr(_module, name)
