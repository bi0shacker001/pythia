"""The copies of games' saves kept on this computer (``droidtop_agent.saves``):
when both a handheld and this computer changed a game's saves, the newest
copy wins and the other is kept here, so nothing is lost."""

from pythia.droidtop.native import submodule


def copies() -> list[dict]:
    """Every game's kept copies: ``key``, ``title``, ``dir`` and ``copies``,
    each with ``device``, ``stamp`` (UTC, ``20261008T224500Z``), ``files``,
    ``path``, ``newest`` and ``reviewed``."""
    return submodule("saves").copies()
