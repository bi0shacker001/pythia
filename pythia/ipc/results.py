"""The shape of what a slot answers, so a client can type it.

A slot's answer is a contract answer (pythia/ipc/ui_contract.py): ``status`` and, when it is ``ok``, the fields the
slot builds beside it. Those fields are declared here as TypedDicts and attached to the slot that builds them with
``@returns(Type)``; ``pythia.ipc.schema`` reads the types back off the slots (with ``typing``'s own introspection)
and writes them into schema/backend-ipc.json, so the schema's result types are the code's, not a second list.

Nothing here is checked at run time by the backend. ``problems`` checks an answer against its type, and the tests
run it over what the slots really answer (tests/_test_ipc_results.py), so a slot that builds a field its type does
not name, or leaves one out, fails there instead of reaching a client as a surprise.

A type describes the ``ok`` answer's fields. The other statuses carry only the contract's own fields
(``envelope`` in the schema). A slot with no ``@returns`` has an answer the schema does not type.
"""

import types
import typing
from collections.abc import Callable
from typing import Any, Literal, TypedDict, get_args, get_origin, get_type_hints


def returns(result_type: type) -> Callable:
    """Marks the slot below as answering ``ok`` with the fields of ``result_type`` (a TypedDict)."""

    def mark(function: Callable) -> Callable:
        function.result_type = result_type  # type: ignore[attr-defined]
        return function

    return mark


def result_type_of(function: Any) -> type | None:
    """The type a slot declared, however many wrappers sit above it (functools.wraps keeps ``__wrapped__``)."""
    while function is not None:
        declared = getattr(function, "result_type", None)
        if declared is not None:
            return declared
        function = getattr(function, "__wrapped__", None)
    return None


# ---- describing a type for the schema -------------------------------------------------------------------------------

_SCALARS = {str: "string", int: "integer", float: "number", bool: "boolean", type(None): "null", Any: "any"}


def _is_typed_dict(tp: Any) -> bool:
    return isinstance(tp, type) and issubclass(tp, dict) and hasattr(tp, "__required_keys__")


def type_json(tp: Any, found: dict[str, type]) -> Any:
    """The JSON of one type; a TypedDict is named and collected in ``found`` for the schema's ``types``."""
    if tp in _SCALARS:
        return _SCALARS[tp]
    if _is_typed_dict(tp):
        found[tp.__name__] = tp
        return {"object": tp.__name__}
    origin = get_origin(tp)
    args = get_args(tp)
    if origin is list:
        return {"array": type_json(args[0], found)}
    if origin is dict:
        return {"map": type_json(args[1], found)}
    if origin is Literal:
        return {"enum": list(args)}
    if origin in (typing.Union, types.UnionType):
        return {"oneOf": [type_json(a, found) for a in args]}
    raise TypeError(f"The type {tp!r} has no schema form; add it to pythia.ipc.results.type_json.")


def describe_object(tp: type, found: dict[str, type]) -> dict[str, Any]:
    """The fields of a TypedDict: each its ``type`` and, when it may be absent, ``optional``."""
    hints = get_type_hints(tp)
    required = getattr(tp, "__required_keys__", frozenset())
    fields: dict[str, Any] = {}
    for name in sorted(hints):
        entry: dict[str, Any] = {"type": type_json(hints[name], found)}
        if name not in required:
            entry["optional"] = True
        fields[name] = entry
    return {"fields": fields}


def describe_all(roots: list[type]) -> dict[str, Any]:
    """Every TypedDict reachable from ``roots``, by name, in one pass (a type may name others)."""
    found: dict[str, type] = {}
    for root in roots:
        found[root.__name__] = root
    done: dict[str, Any] = {}
    while True:
        pending = [name for name in found if name not in done]
        if not pending:
            break
        for name in sorted(pending):
            done[name] = describe_object(found[name], found)
    return {name: done[name] for name in sorted(done)}


# ---- checking an answer against its type ---------------------------------------------------------------------------


def _matches(value: Any, tp: Any, path: str, out: list[str]) -> None:
    if tp is Any:
        return
    if tp is type(None) or tp is None:
        if value is not None:
            out.append(f"{path}: expected null, got {value!r}")
        return
    if tp is bool:
        if not isinstance(value, bool):
            out.append(f"{path}: expected a boolean, got {value!r}")
        return
    if tp is int:
        if isinstance(value, bool) or not isinstance(value, int):
            out.append(f"{path}: expected an integer, got {value!r}")
        return
    if tp is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            out.append(f"{path}: expected a number, got {value!r}")
        return
    if tp is str:
        if not isinstance(value, str):
            out.append(f"{path}: expected text, got {value!r}")
        return
    if _is_typed_dict(tp):
        out.extend(problems(value, tp, path))
        return
    origin, args = get_origin(tp), get_args(tp)
    if origin is list:
        if not isinstance(value, list):
            out.append(f"{path}: expected a list, got {value!r}")
            return
        for index, item in enumerate(value):
            _matches(item, args[0], f"{path}[{index}]", out)
        return
    if origin is dict:
        if not isinstance(value, dict):
            out.append(f"{path}: expected an object, got {value!r}")
            return
        for key, item in value.items():
            _matches(item, args[1], f"{path}.{key}", out)
        return
    if origin is Literal:
        if value not in args:
            out.append(f"{path}: expected one of {list(args)}, got {value!r}")
        return
    if origin in (typing.Union, types.UnionType):
        for option in args:
            attempt: list[str] = []
            _matches(value, option, path, attempt)
            if not attempt:
                return
        out.append(f"{path}: {value!r} is none of {[getattr(a, '__name__', str(a)) for a in args]}")
        return
    out.append(f"{path}: the type {tp!r} cannot be checked")


def problems(value: Any, tp: type, path: str = "$") -> list[str]:
    """What is wrong with ``value`` as a ``tp``: a missing field, a field the type does not name, a wrong type.
    The contract's own fields (status, slot) are not part of the type and are ignored at the top of an answer."""
    if not isinstance(value, dict):
        return [f"{path}: expected an object, got {value!r}"]
    hints = get_type_hints(tp)
    out: list[str] = []
    for name in sorted(getattr(tp, "__required_keys__", frozenset())):
        if name not in value:
            out.append(f"{path}.{name}: missing")
    for name, item in value.items():
        if name not in hints:
            out.append(f"{path}.{name}: not in {tp.__name__}")
            continue
        _matches(item, hints[name], f"{path}.{name}", out)
    return out


def answer_problems(answer: dict, tp: type) -> list[str]:
    """``problems`` for a whole contract answer: its own fields are set aside, the rest is the type's."""
    from pythia.ipc import ui_contract  # noqa: PLC0415

    fields = {k: v for k, v in answer.items() if k not in ui_contract._OWN}  # noqa: SLF001 -- the contract's list
    return problems(fields, tp)


# ---- the fields of each answer -------------------------------------------------------------------------------------


class Card(TypedDict):
    id: str
    title: str
    sort_name: str
    platforms: list[str]
    systems: list[str]
    runner: str | None
    runners: list[str]
    installed: bool
    last_played: int | None
    playtime_hours: float
    playcount: int
    favourite: bool
    art: list[str]
    year: int | None
    added: str | None
    stores: list[str]
    sources: list[str]
    has_update: bool


class GetGamesResult(TypedDict):
    version: str
    unchanged: bool
    games: list[Card]


class SystemRow(TypedDict):
    id: str
    name: str
    system_id: str
    system_name: str
    known: bool
    count: int
    installed: int
    favourites: int
    last_played: int | None


class GetGameSystemsResult(TypedDict):
    version: str
    systems: list[SystemRow]


class StoreId(TypedDict):
    platform: str
    id: str


class VersionRow(TypedDict):
    version: str | None
    variants: int


class MissingField(TypedDict):
    status: str
    message: str


class _GameFields(TypedDict):
    id: str
    title: str
    sort_name: str
    year: int | None
    notes: str
    platforms: list[str]
    runners: list[str]
    stores: list[StoreId]
    ownership: list[str]
    versions: list[VersionRow]
    current_version: Any
    available_updates: list[dict[str, Any]]
    installed: bool
    last_played: int | None
    playtime_hours: float
    playcount: int
    favourite: bool
    sources: list[str]
    added: str | None


class GameFields(_GameFields, total=False):
    # What Pythia records of these, when it has them (the others are under ``missing``).
    genre: Any
    developer: Any
    publisher: Any
    description: Any
    rating: Any


class GetGameDetailResult(TypedDict):
    fields: GameFields
    missing: dict[str, MissingField]


class GetGameArtResult(TypedDict):
    role: str
    path: str
    uri: str
    mime: str
    size: int


class GetGameArtDataResult(TypedDict):
    role: str
    mime: str
    size: int
    offset: int
    length: int
    eof: bool
    data: str


class ArtFile(TypedDict):
    path: str
    uri: str
    mime: str
    size: int


class Screenshot(TypedDict):
    index: int
    path: str
    uri: str
    mime: str
    size: int


class GetGameArtListResult(TypedDict):
    roles: dict[str, ArtFile | None]
    missing: dict[str, str]
    screenshots: list[Screenshot]


class RunningGame(TypedDict):
    game_id: str
    via: str
    started_at: int | None
    pid: int | None
    quittable: bool


class GetRunningGamesResult(TypedDict):
    games: list[RunningGame]
    scope: str


class GameStopped(RunningGame):
    exit_code: int | None


class QuitGameResult(TypedDict):
    game_id: str
    asked: bool


class CancelJobResult(TypedDict):
    job: str
    stopping: bool


class SystemFacet(TypedDict):
    id: str
    name: str
    count: int


class TextFacet(TypedDict):
    value: str
    count: int


class FlagFacet(TypedDict):
    value: bool
    count: int


class Facets(TypedDict):
    system: list[SystemFacet]
    runner: list[TextFacet]
    store: list[TextFacet]
    installed: list[FlagFacet]
    favourite: list[FlagFacet]
    # A decade as {"from", "to", "count"}, and the games with no year as {"unknown": true, "count"}.
    year: list[dict[str, Any]]


class _QueryGamesResult(TypedDict):
    version: str
    total: int
    offset: int
    limit: int
    games: list[Card]


class QueryGamesResult(_QueryGamesResult, total=False):
    facets: Facets  # unless the query said facets: false


class Collection(TypedDict):
    id: str
    name: str
    game_ids: list[str]
    count: int
    created_at: str
    updated_at: str


class GetCollectionsResult(TypedDict):
    version: str
    collections: list[Collection]


class CollectionResult(TypedDict):
    collection: Collection


class _CollectionMembers(TypedDict):
    collection: Collection
    changed: list[str]


class CollectionMembersResult(_CollectionMembers, total=False):
    unknown: list[str]  # add_to_collection only: ids the library does not have


class DeleteCollectionResult(TypedDict):
    collection: None
    deleted: bool


class _CollectionsChanged(TypedDict):
    kind: str
    version: str


class CollectionsChanged(_CollectionsChanged, total=False):
    id: str  # the collection; absent for kind "synced"


# What each event carries, by the name of its signal (the ones whose payload is an object this file declares).
EVENT_PAYLOADS: dict[str, type] = {
    "collectionsChanged": CollectionsChanged,
    "gameStarted": RunningGame,
    "gameStopped": GameStopped,
}
