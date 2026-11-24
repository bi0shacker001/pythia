"""A query over the library's cards, with the facets a shelf screen filters by and what each would leave.

``run(cards, query)`` takes the cards of pythia.gaming_api as the backend holds them in memory and answers in one
pass over them, never touching a record or a file: the games that match (one page of them), how many match in all,
and, for every facet, how many games each value has. A facet's counts leave out the filter on that same facet, as a
filter list needs (picking "Linux" still shows what "Windows" would give), and keep every other filter.

The query is an object, every key optional:

    text         words that must all be in the title (case ignored)
    system       droidtop-platforms system ids (one or a list); a game matches any of them
    runner       runner names, the same way
    store        stores the game is known on (steam, gog, ...), the same way
    installed    true or false
    favourite    true or false
    collection   a collection id (pythia.game_collections): only its games
    year_from, year_to   the game's year, both ends included; a game with no year matches neither
    sort         title (default), last_played, playtime, playcount, year, added
    descending   true for the other way round
    offset, limit   the page; limit defaults to 100 and is at most 1000, 0 gives counts and facets only
    facets       false to leave the facets out
"""

from typing import Any

from pythia import platform_systems

DEFAULT_LIMIT = 100
MAX_LIMIT = 1000
SORTS = ("title", "last_played", "playtime", "playcount", "year", "added")
DECADE = 10
UNKNOWN_YEAR = "unknown"


class QueryError(ValueError):
    """A query that cannot be answered; the message says what is wrong in words for a screen."""


def _strings(query: dict, key: str) -> set[str] | None:
    value = query.get(key)
    if value is None or value == "" or value == []:
        return None
    if isinstance(value, str):
        return {value}
    if isinstance(value, (list, tuple, set)) and all(isinstance(v, str) for v in value):
        return set(value)
    raise QueryError(f"{key} must be a text or a list of texts.")


def _flag(query: dict, key: str) -> bool | None:
    value = query.get(key)
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    raise QueryError(f"{key} must be true or false.")


def _whole(query: dict, key: str, default: int | None = None) -> int | None:
    value = query.get(key)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise QueryError(f"{key} must be a whole number.")
    return value


def _sort_key(sort: str):
    def title(card: dict):
        return (card["sort_name"] or card["title"]).casefold(), card["id"]

    plain = {
        "last_played": lambda c: c["last_played"],
        "playtime": lambda c: c["playtime_hours"],
        "playcount": lambda c: c["playcount"],
        "year": lambda c: c["year"],
        "added": lambda c: c["added"],
    }
    return title, plain.get(sort)


def _ordered(matching: list[dict], sort: str, descending: bool) -> list[dict]:
    title, plain = _sort_key(sort)
    if plain is None:
        return sorted(matching, key=title, reverse=descending)
    # Games with no value for the sort go last whichever way it runs, and ties keep the title order.
    have = [c for c in matching if plain(c) not in (None, "")]
    none = sorted((c for c in matching if plain(c) in (None, "")), key=title)
    have = sorted(have, key=title)
    return sorted(have, key=plain, reverse=descending) + none


def _decade(year: int) -> int:
    return year - year % DECADE


def run(cards: list[dict], query: dict[str, Any] | None, collection_ids: set[str] | None = None) -> dict[str, Any]:
    """The answer to ``query`` over ``cards``; ``collection_ids`` is the member set of the query's collection,
    which the caller reads (a query over cards does not know collections)."""
    query = query or {}
    if not isinstance(query, dict):
        raise QueryError("The query must be an object.")
    words = str(query.get("text") or "").casefold().split()
    systems, runners, stores = _strings(query, "system"), _strings(query, "runner"), _strings(query, "store")
    installed, favourite = _flag(query, "installed"), _flag(query, "favourite")
    year_from, year_to = _whole(query, "year_from"), _whole(query, "year_to")
    offset, limit = _whole(query, "offset", 0), _whole(query, "limit", DEFAULT_LIMIT)
    assert offset is not None and limit is not None  # noqa: S101 -- defaults given
    if offset < 0 or limit < 0:
        raise QueryError("offset and limit cannot be negative.")
    limit = min(limit, MAX_LIMIT)
    sort = str(query.get("sort") or "title")
    if sort not in SORTS:
        raise QueryError(f"sort is one of {', '.join(SORTS)}.")
    descending = query.get("descending")
    if descending is not None and not isinstance(descending, bool):
        raise QueryError("descending must be true or false.")
    wanted = query.get("facets", True)
    if not isinstance(wanted, bool):
        raise QueryError("facets must be true or false.")

    system_names: dict[str, str] = {}
    system_cache: dict[str, dict] = {}
    counts: dict[str, dict] = {"system": {}, "runner": {}, "store": {}, "installed": {}, "favourite": {}, "year": {}}
    matching: list[dict] = []

    def system_name(card: dict, ident: str) -> str:
        # The card names the platforms and their systems in the same order (gaming_api.card), shortest way to a name.
        if ident not in system_names:
            for platform in card["platforms"]:
                found = system_cache.get(platform) or system_cache.setdefault(platform, platform_systems.system_of(platform))
                system_names.setdefault(found["id"], found["name"])
            system_names.setdefault(ident, "Unknown platform" if ident == "unknown" else ident)
        return system_names[ident]

    for card in cards:
        year = card.get("year")
        ok = {
            "text": not words or all(word in card["title"].casefold() for word in words),
            "collection": collection_ids is None or card["id"] in collection_ids,
            "system": systems is None or any(s in systems for s in card["systems"]),
            "runner": runners is None or any(r in runners for r in card["runners"]),
            "store": stores is None or any(s in stores for s in card["stores"]),
            "installed": installed is None or card["installed"] == installed,
            "favourite": favourite is None or card["favourite"] == favourite,
            "year": (year_from is None and year_to is None)
            or (year is not None and (year_from is None or year >= year_from) and (year_to is None or year <= year_to)),
        }
        failed = [name for name, passed in ok.items() if not passed]
        if not failed:
            matching.append(card)
        if not wanted or len(failed) > 1 or (failed and failed[0] in ("text", "collection")):
            continue
        # Counted for every facet when it matches, and for the one facet it fails alone (that filter is left out).
        for facet, values in (
            ("system", card["systems"]),
            ("runner", card["runners"]),
            ("store", card["stores"]),
            ("installed", [card["installed"]]),
            ("favourite", [card["favourite"]]),
            ("year", [_decade(year) if year is not None else UNKNOWN_YEAR]),
        ):
            if failed and failed[0] != facet:
                continue
            for value in values:
                counts[facet][value] = counts[facet].get(value, 0) + 1
                if facet == "system":
                    system_name(card, value)

    ordered = _ordered(matching, sort, bool(descending))
    answer: dict[str, Any] = {
        "total": len(ordered),
        "offset": offset,
        "limit": limit,
        "games": ordered[offset : offset + limit],
    }
    if wanted:
        answer["facets"] = {
            "system": [
                {"id": ident, "name": system_names.get(ident, ident), "count": n}
                for ident, n in sorted(counts["system"].items(), key=lambda kv: (kv[0] == "unknown", kv[0]))
            ],
            "runner": [{"value": v, "count": n} for v, n in sorted(counts["runner"].items())],
            "store": [{"value": v, "count": n} for v, n in sorted(counts["store"].items())],
            "installed": [{"value": v, "count": n} for v, n in sorted(counts["installed"].items(), reverse=True)],
            "favourite": [{"value": v, "count": n} for v, n in sorted(counts["favourite"].items(), reverse=True)],
            "year": [
                {"from": d, "to": d + DECADE - 1, "count": n}
                for d, n in sorted((d, n) for d, n in counts["year"].items() if d != UNKNOWN_YEAR)
            ]
            + ([{"unknown": True, "count": counts["year"][UNKNOWN_YEAR]}] if UNKNOWN_YEAR in counts["year"] else []),
        }
    return answer
