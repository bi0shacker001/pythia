"""The copies of games' saves kept on this computer (``pythia._native.saves``):
when both a handheld and this computer changed a game's saves, the newest
copy wins and the other is kept here, so nothing is lost."""

from pythia.droidtop.native import plain_path, submodule


def copies() -> list[dict]:
    """Every game's kept copies: ``key``, ``title``, ``dir`` and ``copies``,
    each with ``device``, ``stamp`` (UTC, ``20261008T224500Z``), ``files``,
    ``path``, ``newest`` and ``reviewed``. Paths are plain (plain_path)."""
    games = submodule("saves").copies()
    for game in games:
        game["dir"] = plain_path(game.get("dir"))
        for copy in game.get("copies") or []:
            copy["path"] = plain_path(copy.get("path"))
    return games


def lookup(key: str, title: str = "", fetch: bool = False) -> dict:
    """Where a game keeps its saves here: ``patterns`` (None when nothing is
    known) and the ``files`` there now, each with ``name`` and ``path``;
    ``syncthing``, the locations the Syncthing here already syncs (``pattern``,
    ``label``, ``path``), left to it, and ``all_by_syncthing``. With
    ``fetch``, the Ludusavi manifest is fetched first when there is none yet.
    May rescan (slow)."""
    return submodule("saves").lookup(key, title, fetch=fetch)


def restore(key: str, path: str, title: str = "") -> int:
    """Puts the kept copy at ``path`` back where the game keeps its saves;
    what is there now is kept first as this computer's own copy. Returns how
    many files it put back. May rescan (slow)."""
    return submodule("saves").restore(key, path, title)


def back_up(key: str, title: str = "") -> dict:
    """Keeps a copy of a game's saves as they are here now, as this computer's
    own copy: ``files`` copied (0 when it has none here) and ``known`` (False
    when no save location is known for it)."""
    return submodule("saves").back_up(key=key, title=title)


def back_up_all(games: list[dict]) -> dict:
    """back_up() for each of ``games`` (``key``, ``title``): how many were
    ``backed_up`` (with ``files`` in all), had ``no_saves`` here yet, had
    ``unknown`` save locations, and each ``failure`` (title and message)."""
    from pythia.droidtop.native import AgentError  # noqa: PLC0415

    out: dict = {"backed_up": 0, "files": 0, "no_saves": 0, "unknown": 0, "failures": []}
    for game in games:
        try:
            done = back_up(game["key"], game.get("title", ""))
        except AgentError as ex:
            out["failures"].append({"title": game.get("title") or game["key"], "message": str(ex)})
            continue
        if not done["known"]:
            out["unknown"] += 1
        elif done["files"]:
            out["backed_up"] += 1
            out["files"] += done["files"]
        else:
            out["no_saves"] += 1
    return out
