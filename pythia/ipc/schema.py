"""The method and event list of the backend channel, read from the Backend itself so it cannot drift.

`describe(Backend)` reads explicit IPC method and event declarations and the Python
signatures (for the argument names and which have defaults). schema/backend-ipc.json is its checked-in output; the
Kotlin client is generated from that file, and tests/_test_ipc_schema.py fails when the Backend changes without it.

    python -m pythia.ipc.schema            write schema/backend-ipc.json
    python -m pythia.ipc.schema --check    exit 1 when the file is out of date

What the file says about results: every call is answered with a backend-contract answer (`status` ok,
unavailable, error or pending; pythia/ipc/ui_contract.py). A slot that returns a bare string is wrapped as
`{"status": "ok", "value": "..."}` and one that returns nothing as `{"status": "ok"}`. The fields beside `status`
are the slot's own. The method declarations do not carry them (`returns` says only `answer`, `string` or `none`),
so a slot that builds them declares their type with `@results.returns(Type)` (pythia/ipc/results.py): the method
then has `result`, the name of a type, and the file's `types` describe every such type field by field, read off
the TypedDicts with `typing`'s own introspection. `envelope` is what every answer has, whatever its status. An event
whose payload is declared in `results.EVENT_PAYLOADS` has `payload`, a type name. A slot without a declared type
has an answer this file does not type.
"""

import inspect
import json
import sys
from pathlib import Path
from typing import Any

from pythia.ipc import CONTRACT_PIN, PROTOCOL, results, ui_contract
from pythia.ipc.declarations import Event

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schema" / "backend-ipc.json"

_ANNOTATIONS = {str: "string", bool: "boolean", int: "integer", float: "number"}


def _answers_later(function: Any) -> bool:
    """True for a slot made with Backend's _off_the_main_thread, however many wrappers sit above it."""
    while function is not None:
        if getattr(function, "answers_later", False):
            return True
        function = getattr(function, "__wrapped__", None)
    return False


def _params(cls: type, name: str, wire_types: tuple[str, ...]) -> list[dict[str, Any]]:
    function = getattr(cls, name)
    python = [p for p in inspect.signature(function).parameters.values() if p.name != "self"]
    params: list[dict[str, Any]] = []
    for index, wire_type in enumerate(wire_types):
        entry: dict[str, Any] = {"name": python[index].name if index < len(python) else f"arg{index}"}
        entry["type"] = wire_type
        if index < len(python) and python[index].default is not inspect.Parameter.empty:
            entry["optional"] = True
        params.append(entry)
    # A Python default the IPC declaration does not list (start_watch's plugin_id): callable, so optional.
    for extra in python[len(wire_types) :]:
        if extra.default is inspect.Parameter.empty:
            raise ValueError(f"{name}: the parameter {extra.name} is not in its IPC declaration.")
        params.append({"name": extra.name, "type": _ANNOTATIONS.get(extra.annotation, "any"), "optional": True})
    return params


def _envelope() -> dict[str, Any]:
    """What every answer has beside the slot's own fields, from the contract's own list of them."""
    fields: dict[str, Any] = {"status": {"type": {"enum": list(ui_contract.STATUSES)}}}
    for name in ui_contract._OWN:  # noqa: SLF001 -- the contract's list of its own fields
        if name != "status":
            fields[name] = {"type": "string", "optional": True}
    return {"fields": fields}


def describe(cls: type) -> dict[str, Any]:
    """Read a backend's wire declarations, sorting its methods and events by name."""
    methods: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    roots: list[type] = []
    for name, function in vars(cls).items():
        declaration = getattr(function, "__ipc_method__", None)
        if declaration is None:
            continue
        params, returns = declaration
        entry: dict[str, Any] = {"name": name, "params": _params(cls, name, params), "returns": returns}
        if _answers_later(function):
            entry["async"] = True
        result_type = results.result_type_of(function)
        if result_type is not None:
            entry["result"] = result_type.__name__
            roots.append(result_type)
        methods.append(entry)
    for name, declaration in vars(cls).items():
        if isinstance(declaration, Event):
            event: dict[str, Any] = {"name": name, "params": [{"type": t} for t in declaration.params]}
            if name in results.EVENT_PAYLOADS:
                event["payload"] = results.EVENT_PAYLOADS[name].__name__
                roots.append(results.EVENT_PAYLOADS[name])
            events.append(event)
    methods.sort(key=lambda m: m["name"])
    events.sort(key=lambda e: e["name"])
    return {
        "protocol": PROTOCOL,
        "contract": CONTRACT_PIN,
        "envelope": _envelope(),
        "types": results.describe_all(roots),
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
