"""What a gaming front end reads of the library: the card of every game in one snapshot, the systems they
belong to, one game's detail and its art (mytop-project/tracker#920 step 3; the Gaming UI's library interfaces).

Everything here is computed from the records the backend already holds in memory (Backend.refresh_games), so a
list is one pass over them and never a query or a disk read per game. The art lookups are the only disk reads and
they are asked for one game at a time. A value Pythia does not have is absent or None, never made up; a detail says
which fields it cannot give and why (``missing``).
"""

import datetime
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from pythia import platform_systems
from pythia.util.strings import slugify

# What a game page reads that Pythia's records do not carry (docs: the entry shape in pythia.datadir has the name,
# sort name, year, notes, store ids, ownership, versions and variants). Listed with the reason each is missing.
NOT_KEPT = {
    "genre": "Pythia does not record a game's genre.",
    "developer": "Pythia does not record a game's developer.",
    "publisher": "Pythia does not record a game's publisher.",
    "description": "Pythia does not record a game's description.",
    "rating": "Pythia does not record a game's rating.",
}
NO_PLATFORM = "Unknown platform"

ART_ROLES = ("cover", "banner", "logo", "background", "icon", "screenshot")
_NO_SCREENSHOTS = "Pythia keeps no screenshots of a game."
_EXTENSIONS = (".jpg", ".png", ".webp")
_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


def _variants(entry: dict) -> list[dict]:
    return [v for group in entry.get("versions") or [] for v in group.get("variants") or []]


def _epoch(value: Any) -> int | None:
    """A played-at time as epoch seconds: Lutris keeps whole seconds, Pythia's own records a UTC text."""
    if value is None or value == "" or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value) if value > 0 else None
    text = str(value).strip()
    if text.isdigit():
        return int(text) or None
    try:
        parsed = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return int(parsed.timestamp())


def _count(value: Any) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def _hours(value: Any) -> float:
    try:
        return max(float(value or 0.0), 0.0)
    except (TypeError, ValueError):
        return 0.0


def _year(entries: list[dict]) -> int | None:
    years = []
    for entry in entries:
        for variant in _variants(entry):
            try:
                year = int(variant.get("year") or 0)
            except (TypeError, ValueError):
                continue
            if year > 0:
                years.append(year)
    return min(years) if years else None


def _platforms(entries: list[dict]) -> list[str]:
    seen: dict[str, None] = {}
    for entry in entries:
        for variant in _variants(entry):
            for name in [variant.get("platform"), *(variant.get("platforms") or [])]:
                if name and str(name) not in seen:
                    seen[str(name)] = None
    return list(seen)


def _installed_here(entries: list[dict], system_uuid: str) -> bool:
    return any(
        variant.get("path") or variant.get("installed")
        for entry in entries
        if entry.get("system_uuid") == system_uuid
        for variant in _variants(entry)
    )


def _title(entries: list[dict], system_uuid: str) -> str:
    own = [e for e in entries if e.get("system_uuid") == system_uuid]
    for entry in [*own, *entries]:
        if entry.get("canonical_name"):
            return str(entry["canonical_name"])
    return ""


def card(game: dict, system_uuid: str) -> dict:
    """One game's card, from one record of Backend._games (``game_id``, ``entries``, ``cover``, ``favourite``)."""
    entries = game.get("entries") or []
    variants = [v for e in entries for v in _variants(e)]
    title = _title(entries, system_uuid)
    sortname = next((e["sortname"] for e in entries if e.get("sortname")), "")
    played = [t for t in (_epoch(v.get("lastplayed")) for v in variants) if t]
    added = sorted(str(v["detected_at"]) for v in variants if v.get("detected_at"))
    runners = list(dict.fromkeys(str(v["runner"]) for v in variants if v.get("runner")))
    stores = list(
        dict.fromkeys(str(p["platform"]) for e in entries for p in e.get("platform_ids") or [] if p.get("platform"))
    )
    sources = list(
        dict.fromkeys(
            str(v["source"]["kind"]) for v in variants if isinstance(v.get("source"), dict) and v["source"].get("kind")
        )
    )
    platforms = _platforms(entries)
    systems = list(dict.fromkeys(platform_systems.system_of(name)["id"] for name in platforms))
    return {
        "id": game["game_id"],
        "title": title,
        "sort_name": sortname or title.lower(),
        "platforms": platforms,
        # The droidtop-platforms system of each platform (pythia.platform_systems), what a shelf is grouped by.
        "systems": systems or ["unknown"],
        "runner": runners[0] if runners else None,
        "runners": runners,
        "installed": _installed_here(entries, system_uuid),
        "last_played": max(played) if played else None,
        "playtime_hours": round(sum(_hours(v.get("playtime")) for v in variants), 4),
        # How many times it was played, recorded with each play (a game never played here has none).
        "playcount": sum(_count(v.get("playcount")) for v in variants),
        "favourite": bool(game.get("favourite")),
        # The roles known without touching the disk again: the cover was looked up when the library was read.
        # The others are asked for one game at a time (get_game_art).
        "art": ["cover"] if game.get("cover") else [],
        "year": _year(entries),
        "added": added[0] if added else None,
        "stores": stores,
        "sources": sources,
        "has_update": any(e.get("available_updates") for e in entries),
    }


def cards(games: list[dict], system_uuid: str) -> list[dict]:
    return [card(g, system_uuid) for g in games]


def version(card_list: list[dict]) -> str:
    """A short text that changes when any card does, so a client keeps its copy while it matches."""
    text = json.dumps(card_list, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]  # noqa: S324 -- a change marker, not a secret


def _droidtop_system(name: str) -> dict:
    """The platform as a droidtop-platforms system: ``system_id``, ``system_name`` and whether it is ``known`` there."""
    if name == NO_PLATFORM:
        return {"system_id": "unknown", "system_name": NO_PLATFORM, "known": False}
    found = platform_systems.system_of(name)
    return {"system_id": found["id"], "system_name": found["name"], "known": found["known"]}


def systems(card_list: list[dict]) -> list[dict]:
    """The platforms the library has, each with the games on it, in one pass: ``id``, ``name``, ``count``,
    ``installed``, ``favourites``, ``last_played``. A game on several platforms counts under each."""
    found: dict[str, dict] = {}
    for item in card_list:
        for name in item["platforms"] or [NO_PLATFORM]:
            system = found.setdefault(
                name,
                {
                    "id": slugify(name) if name != NO_PLATFORM else "unknown",
                    "name": name,
                    **_droidtop_system(name),
                    "count": 0,
                    "installed": 0,
                    "favourites": 0,
                    "last_played": None,
                },
            )
            system["count"] += 1
            system["installed"] += 1 if item["installed"] else 0
            system["favourites"] += 1 if item["favourite"] else 0
            if item["last_played"] and (system["last_played"] or 0) < item["last_played"]:
                system["last_played"] = item["last_played"]
    return sorted(found.values(), key=lambda s: (s["name"] == NO_PLATFORM, s["name"].lower()))


def detail(game: dict, system_uuid: str) -> dict:
    """One game's page: what Pythia has under ``fields`` and, for each thing a page shows that it does not keep,
    why under ``missing`` (each an ``unavailable`` answer). Nothing is guessed."""
    entries = game.get("entries") or []
    summary = card(game, system_uuid)
    versions = [
        {"version": group.get("version"), "variants": len(group.get("variants") or [])}
        for e in entries
        for group in e.get("versions") or []
    ]
    ownership = list(
        dict.fromkeys(str(o["platform"]) for e in entries for o in e.get("ownership") or [] if o.get("platform"))
    )
    store_ids = [
        {"platform": str(p["platform"]), "id": str(p.get("id") or "")}
        for e in entries
        for p in e.get("platform_ids") or []
        if p.get("platform")
    ]
    updates = [u for e in entries for u in e.get("available_updates") or []]
    notes = next((str(e["notes"]) for e in entries if e.get("notes")), "")
    current = next((e["current_version"] for e in entries if e.get("current_version")), None)
    fields: dict[str, Any] = {
        "id": summary["id"],
        "title": summary["title"],
        "sort_name": summary["sort_name"],
        "year": summary["year"],
        "notes": notes,
        "platforms": summary["platforms"],
        "runners": summary["runners"],
        "stores": store_ids,
        "ownership": ownership,
        "versions": versions,
        "current_version": current,
        "available_updates": updates,
        "installed": summary["installed"],
        "last_played": summary["last_played"],
        "playtime_hours": summary["playtime_hours"],
        "playcount": summary["playcount"],
        "favourite": summary["favourite"],
        "sources": summary["sources"],
        "added": summary["added"],
    }
    missing: dict[str, dict] = {}
    for name, reason in NOT_KEPT.items():
        kept = next((e[name] for e in entries if e.get(name) not in (None, "", 0)), None)
        if kept is not None:
            fields[name] = kept
        else:
            missing[name] = {"status": "unavailable", "message": reason}
    return {"fields": fields, "missing": missing}


# ---- art ------------------------------------------------------------------------------------------------------


def _first_file(*paths: Path) -> Path | None:
    return next((p for p in paths if p.is_file()), None)


def _in_folder(folder: str | None, slug: str) -> Path | None:
    if not folder or not slug:
        return None
    return _first_file(*(Path(folder) / f"{slug}{ext}" for ext in _EXTENSIONS))


def _steam(appid: str, *names: str) -> Path | None:
    """A file of Steam's own library art for a Steam game, in either layout (flat, or a folder per game)."""
    from pythia import covers  # noqa: PLC0415

    cache = covers._steam_cache()  # noqa: SLF001 -- the one place that knows where Steam keeps it
    if not cache or not appid.isdigit():
        return None
    return _first_file(*(p for name in names for p in (cache / f"{appid}_{name}", cache / appid / name)))


def art_path(entries: list[dict], role: str) -> tuple[Path | None, str | None]:
    """The file of ``role`` for a game recorded as ``entries``, from what is already on this computer, and the
    reason when there is none. Nothing is downloaded or made."""
    from pythia import covers, settings  # noqa: PLC0415

    if role not in ART_ROLES:
        return None, f"There is no art role {role!r}; the roles are {', '.join(ART_ROLES)}."
    if role == "screenshot":
        return None, _NO_SCREENSHOTS
    steam_ids = [
        str(p["id"])
        for e in entries
        for p in e.get("platform_ids") or []
        if p.get("platform") == "steam" and p.get("id")
    ]
    slugs = [s for s in dict.fromkeys(slugify(e.get("canonical_name") or "") for e in entries) if s]
    found: Path | None = None
    if role == "cover":
        for entry in entries:
            found = covers.cover_for(entry.get("canonical_name") or "", entry.get("platform_ids") or [])
            if found:
                break
    elif role == "banner":
        found = next((p for p in (_in_folder(settings.BANNER_PATH, s) for s in slugs) if p), None)
        found = found or next((p for p in (_steam(i, "header.jpg") for i in steam_ids) if p), None)
    elif role == "icon":
        found = next((p for p in (_first_file(Path(settings.ICON_PATH) / f"pythia_{s}.png") for s in slugs) if p), None)
    elif role == "logo":
        found = next((p for p in (_steam(i, "logo.png") for i in steam_ids) if p), None)
    elif role == "background":
        found = next((p for p in (_steam(i, "library_hero.jpg") for i in steam_ids) if p), None)
    if found:
        return found, None
    where = "Lutris's art folders or Steam's library art" if role in ("cover", "banner") else "Steam's library art"
    if role == "icon":
        where = "Pythia's icon folder"
    return None, f"There is no {role} for this game in {where} on this computer."


def art(entries: list[dict], role: str) -> dict:
    """The answer for one art role: ``path``, ``uri``, ``mime`` and ``size`` of the file, or ``unavailable``."""
    from pythia.ipc import ui_contract  # noqa: PLC0415

    path, reason = art_path(entries, role)
    if path is None:
        return ui_contract.unavailable(reason or "No art.", slot="get_game_art")
    return ui_contract.ok(
        slot="get_game_art",
        role=role,
        path=str(path),
        uri=path.as_uri(),
        mime=_MIME.get(path.suffix.lower(), "application/octet-stream"),
        size=os.path.getsize(path),
    )


# A client on another computer cannot open the file ``art`` names, so the bytes are read in chunks it asks for
# (base64 in the answer), each well inside the channel's frame limit.
DEFAULT_CHUNK = 256 * 1024
MAX_CHUNK = 1024 * 1024


def art_bytes(entries: list[dict], role: str, offset: int = 0, length: int = DEFAULT_CHUNK) -> dict:
    """``length`` bytes of the art file of ``role`` from ``offset``, as ``data`` (base64) with the file's ``mime``
    and whole ``size``, how many bytes this chunk holds (``length``) and whether it ends the file (``eof``)."""
    import base64  # noqa: PLC0415

    from pythia.ipc import ui_contract  # noqa: PLC0415

    path, reason = art_path(entries, role)
    if path is None:
        return ui_contract.unavailable(reason or "No art.", slot="get_game_art_data")
    if offset < 0 or length < 1:
        return ui_contract.error(
            "The offset cannot be negative and the length must be at least 1.",
            slot="get_game_art_data",
            code="bad-range",
        )
    length = min(length, MAX_CHUNK)
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as handle:
            handle.seek(offset)
            chunk = handle.read(length)
    except OSError as ex:
        return ui_contract.error(f"The art file could not be read: {ex}", slot="get_game_art_data", code="unreadable")
    if offset > size:
        return ui_contract.error(
            f"The offset {offset} is past the end of the {size} byte file.", slot="get_game_art_data", code="bad-range"
        )
    return ui_contract.ok(
        slot="get_game_art_data",
        role=role,
        mime=_MIME.get(path.suffix.lower(), "application/octet-stream"),
        size=size,
        offset=offset,
        length=len(chunk),
        eof=offset + len(chunk) >= size,
        data=base64.b64encode(chunk).decode("ascii"),
    )
