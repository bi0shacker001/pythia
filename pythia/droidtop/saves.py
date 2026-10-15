"""The copies of games' saves kept on this computer (``droidtop_agent.saves``):
when both a handheld and this computer changed a game's saves, the newest
copy wins and the other is kept here, so nothing is lost."""

from pythia.droidtop.native import submodule


def copies() -> list[dict]:
    """Every game's kept copies: ``key``, ``title``, ``dir`` and ``copies``,
    each with ``device``, ``stamp`` (UTC, ``20261008T224500Z``), ``files``,
    ``path``, ``newest`` and ``reviewed``."""
    return submodule("saves").copies()


def lookup(key: str, title: str = "", fetch: bool = False) -> dict:
    """Where a game keeps its saves here: ``patterns`` (None when nothing is
    known) and the ``files`` there now, each with ``name`` and ``path``. With
    ``fetch``, the Ludusavi manifest is fetched first when there is none yet.
    May rescan (slow)."""
    return submodule("saves").lookup(key, title, fetch=fetch)


def restore(key: str, path: str, title: str = "") -> int:
    """Puts the kept copy at ``path`` back where the game keeps its saves;
    what is there now is kept first as this computer's own copy. Returns how
    many files it put back. May rescan (slow)."""
    return submodule("saves").restore(key, path, title)
