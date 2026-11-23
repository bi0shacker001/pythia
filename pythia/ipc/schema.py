"""The method and event list of the backend channel, read from the Backend itself so it cannot drift.

`describe(Backend)` walks the Qt meta-object (every `@Slot` and `Signal` the HTML UI can reach) and the Python
signatures (for the argument names and which have defaults). schema/backend-ipc.json is its checked-in output; the
Kotlin client is generated from that file, and tests/_test_ipc_schema.py fails when the Backend changes without it.

    python -m pythia.ipc.schema            write schema/backend-ipc.json
    python -m pythia.ipc.schema --check    exit 1 when the file is out of date

What the file says about results: every call is answered with a backend-contract answer (`status` ok,
unavailable, error or pending; pythia/ipc/ui_contract.py). A slot that returns a bare string is wrapped as
`{"status": "ok", "value": "..."}` and one that returns nothing as `{"status": "ok"}`. The fields beside `status`
are the slot's own and are not in the Slot declarations, so the schema does not type them (`returns` says only
`answer`, `string` or `none`); typing them is the Kotlin client's job, slot by slot.
"""

import inspect
import json
import sys
from pathlib import Path
from typing import Any

from pythia.ipc import CONTRACT_PIN, PROTOCOL

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schema" / "backend-ipc.json"

# What a Qt type is on the wire (JSON): QVariant is any JSON value.
_TYPES = {"QString": "string", "bool": "boolean", "int": "integer", "double": "number", "QVariant": "any"}
_RETURNS = {"QVariant": "answer", "QString": "string", "void": "none"}
_ANNOTATIONS = {str: "string", bool: "boolean", int: "integer", float: "number"}


def _text(value: Any) -> str:
    return bytes(value).decode() if not isinstance(value, str) else value


def _wire_type(qt_type: str) -> str:
    if qt_type not in _TYPES:
        raise ValueError(f"The Qt type {qt_type!r} has no wire type; add it to pythia.ipc.schema._TYPES.")
    return _TYPES[qt_type]


def _answers_later(function: Any) -> bool:
    """True for a slot made with Backend's _off_the_main_thread, however many wrappers sit above it."""
    while function is not None:
        if getattr(function, "answers_later", False):
            return True
        function = getattr(function, "__wrapped__", None)
    return False


def _params(cls: type, name: str, qt_types: list[str]) -> list[dict[str, Any]]:
    function = getattr(cls, name)
    python = [p for p in inspect.signature(function).parameters.values() if p.name != "self"]
    params: list[dict[str, Any]] = []
    for index, qt_type in enumerate(qt_types):
        entry: dict[str, Any] = {"name": python[index].name if index < len(python) else f"arg{index}"}
        entry["type"] = _wire_type(qt_type)
        if index < len(python) and python[index].default is not inspect.Parameter.empty:
            entry["optional"] = True
        params.append(entry)
    # A Python default the @Slot declaration does not list (start_watch's plugin_id): callable, so optional.
    for extra in python[len(qt_types) :]:
        if extra.default is inspect.Parameter.empty:
            raise ValueError(f"{name}: the parameter {extra.name} is not in its @Slot declaration.")
        params.append({"name": extra.name, "type": _ANNOTATIONS.get(extra.annotation, "any"), "optional": True})
    return params


def describe(cls: type) -> dict[str, Any]:
    """The schema of a QObject class: its slots as methods, its signals as events, each sorted by name."""
    from PySide6.QtCore import QMetaMethod  # noqa: PLC0415

    meta = cls.staticMetaObject  # type: ignore[attr-defined]
    methods: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    for index in range(meta.methodOffset(), meta.methodCount()):
        method = meta.method(index)
        name = _text(method.name())
        qt_types = [_text(t) for t in method.parameterTypes()]
        if method.methodType() == QMetaMethod.MethodType.Slot:
            returns = _text(method.typeName())
            if returns not in _RETURNS:
                raise ValueError(f"{name}: the result type {returns!r} is not one of {sorted(_RETURNS)}.")
            entry: dict[str, Any] = {"name": name, "params": _params(cls, name, qt_types), "returns": _RETURNS[returns]}
            if _answers_later(getattr(cls, name)):
                entry["async"] = True
            methods.append(entry)
        elif method.methodType() == QMetaMethod.MethodType.Signal:
            events.append({"name": name, "params": [{"type": _wire_type(t)} for t in qt_types]})
    methods.sort(key=lambda m: m["name"])
    events.sort(key=lambda e: e["name"])
    return {
        "protocol": PROTOCOL,
        "contract": CONTRACT_PIN,
        "methods": methods,
        "events": events,
    }


def backend_schema() -> dict[str, Any]:
    from pythia.ui.backend import Backend  # noqa: PLC0415

    return describe(Backend)


def render(schema: dict[str, Any]) -> str:
    return json.dumps(schema, indent=2, ensure_ascii=False) + "\n"


def load(path: Path = SCHEMA_PATH) -> dict[str, Any]:
    return json.loads(path.read_text("utf-8"))


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    text = render(backend_schema())
    if "--check" in argv:
        current = SCHEMA_PATH.read_text("utf-8") if SCHEMA_PATH.exists() else ""
        if current != text:
            print(f"{SCHEMA_PATH} is out of date; run: python -m pythia.ipc.schema", file=sys.stderr)
            return 1
        return 0
    SCHEMA_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCHEMA_PATH.write_text(text, encoding="utf-8", newline="\n")
    print(f"Wrote {SCHEMA_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
