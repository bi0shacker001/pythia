"""Loading ``pythia._native``, Pythia's droidtop side (``native/``).

It is built with Pythia (setuptools-rust, from droidtop-agent's crates) and
holds Pythia's droidtop state in its own folder, ``DATA_DIR/droidtop``. Its
refusals raise ``pythia._native.AgentError``, which is this package's
``AgentError``. A Pythia run from a source tree that was never built has no
such module: ``submodule()`` then raises an ``AgentError`` of this package's
own saying how to build it.
"""

import os
from types import ModuleType

from pythia import settings

BUILD_HINT = "build it with `pip install -e .` (it needs a Rust toolchain, 1.85 or newer)"
FOLDER = os.path.join(settings.DATA_DIR, "droidtop")

try:  # built with Pythia; missing only in a source tree that was never built
    from pythia import _native as _module  # type: ignore[attr-defined]

    _missing: str | None = None
except ImportError as ex:  # pragma: no cover - depends on the build
    _module = None
    _missing = f"Pythia's droidtop module is not built ({ex}); {BUILD_HINT}"

if _module is not None:
    AgentError = _module.AgentError
else:

    class AgentError(Exception):  # type: ignore[no-redef]
        """A droidtop call that was refused, in words a screen can show."""


_folder_named = False


def available() -> bool:
    return _module is not None


def why_unavailable() -> str | None:
    return _missing


def submodule(name: str) -> ModuleType:
    """``pythia._native.<name>`` over Pythia's droidtop folder, or AgentError
    saying why the module is missing."""
    global _folder_named
    if _module is None:
        raise AgentError(_missing or "Pythia's droidtop module is not built")
    if not _folder_named:
        _module.use_folder(path=FOLDER)
        _folder_named = True
    return getattr(_module, name)


def plain_path(path):
    """A Windows path as people write it: without the extended-length
    prefix (``\\\\?\\G:\\games`` is ``G:\\games``; ``\\\\?\\UNC\\server\\share`` is
    ``\\\\server\\share``) that the native side's canonical paths carry
    (Droidtop/tracker#538 retest S1, S2). Anything else is returned as it is."""
    if not isinstance(path, str):
        return path
    if path.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path[8:]
    if path.startswith("\\\\?\\"):
        return path[4:]
    return path
