"""The person's favourites (owner, 2026-10-23): a library belongs to one
person, so a favourite is theirs and follows them to every computer and
handheld.

Each computer writes only its own record (pythia.datadir), so each record
keeps the mark it last saw with when it was made,
``"favourite": {"on": true, "at": "<UTC time>"}``, and the newest mark across
a game's records is the game's. Un-marking travels the same way as marking.
cache.db indexes the marks (``favourites``, one row per record).

droidtop keeps the same thing as the ``favourite`` mark on its shared library,
last writer wins (droidtop-agent docs/DESIGN.md section 7): a mark made here
is set there too, and a newer one made on a handheld is written into this
computer's record, so other computers sharing the library folder get it.
"""

import datetime

from pythia import datadir
from pythia.database import games as games_db
from pythia.identity import get_system_uuid
from pythia.util.log import logger
from pythia.util.strings import slugify


def now() -> str:
    """A mark's time: UTC to the millisecond, in one format so marks compare as text."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds")


def from_ms(ms: int) -> str:
    return datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc).isoformat(timespec="milliseconds")


def mark_of(entries: list[dict]) -> dict | None:
    """The newest mark across a game's records, or None when none has one."""
    marks = [e["favourite"] for e in entries if isinstance(e.get("favourite"), dict) and e["favourite"].get("at")]
    return max(marks, key=lambda m: m["at"]) if marks else None


def is_favourite(entries: list[dict]) -> bool:
    mark = mark_of(entries)
    return bool(mark and mark.get("on"))


def _own_entry(game_id: str, entries: list[dict]) -> dict:
    """This computer's record of the game; a game it knows only from another
    computer's record gets one of its own, holding just the name and store ids."""
    uuid = get_system_uuid()
    for entry in entries:
        if entry.get("system_uuid") == uuid:
            return entry
    theirs = entries[0]
    name = theirs.get("canonical_name") or "?"
    entry = games_db._new_entry(game_id, name, slugify(name))
    entry["platform_ids"] = list(theirs.get("platform_ids") or [])
    for key in ("game_uuid", "game_id_source"):
        if theirs.get(key):
            entry[key] = theirs[key]
    return entry


def set_favourite(game_id: str, on: bool, at: str | None = None, tell_handhelds: bool = True) -> dict:
    """Marks the game a favourite, or not, as of ``at`` (now by default), in
    this computer's record, and on droidtop's library unless the mark came
    from there."""
    entries = datadir.list_known_games().get(game_id)
    if not entries:
        return {"status": "error", "message": "No record of that game."}
    entry = _own_entry(game_id, entries)
    entry["favourite"] = {"on": bool(on), "at": at or now()}
    games_db.save_entry_and_sync_cache(entry)
    if tell_handhelds:
        _tell_handhelds(entry, on)
    return {"status": "ok", "favourite": bool(on)}


def _tell_handhelds(entry: dict, on: bool) -> None:
    from pythia.droidtop.library import game_key
    from pythia.droidtop.native import AgentError, submodule

    try:
        submodule("store").set_mark(game_key(entry), "favourite", bool(on))
    except AgentError as ex:  # No droidtop module here: the mark stays in the records.
        logger.debug("Favourite not sent to droidtop: %s", ex)


def take_from_handhelds() -> int:
    """Writes into this computer's records each ``favourite`` mark on
    droidtop's library that is newer than the records' own; returns how many."""
    from pythia.droidtop.library import game_key
    from pythia.droidtop.native import AgentError, submodule

    try:
        library = submodule("store").library()
    except AgentError:
        return 0
    taken = 0
    for game_id, entries in datadir.list_known_games().items():
        keys = {game_key(e) for e in entries}
        for key in keys:
            mark = ((library.get(key) or {}).get("marks") or {}).get("favourite")
            if not mark or not isinstance(mark.get("value"), bool):
                continue
            at = from_ms(int((mark.get("at") or {}).get("ms") or 0))
            ours = mark_of(entries)
            if ours is not None and bool(ours.get("on")) == mark["value"]:
                break  # Already the same here (the mark this computer sent, come back).
            if ours is None or at > ours["at"]:
                if ours is None and not mark["value"]:
                    continue  # Never marked anywhere: nothing to write.
                set_favourite(game_id, mark["value"], at=at, tell_handhelds=False)
                taken += 1
            break
    return taken
