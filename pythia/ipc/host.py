"""Start the backend channel independently of the frontend toolkit."""

from collections.abc import Callable
from threading import RLock
from typing import Any

from pythia import instance
from pythia.ipc.schema import describe
from pythia.ipc.server import Server


def start(
    backend: Any,
    where: str | None = None,
    dispatch: Callable[[Callable[[], Any]], Any] | None = None,
) -> Server | None:
    """Publish the declared backend methods and events until the caller stops the server."""
    schema = describe(type(backend))
    handlers = {entry["name"]: getattr(backend, entry["name"]) for entry in schema["methods"]}
    lock = RLock()

    def serialized(call: Callable[[], Any]) -> Any:
        with lock:
            return call()

    server = Server(
        schema,
        handlers,
        instance.channel_key(),
        call_on_main=dispatch or serialized,
        where=where,
        pairing_call=getattr(backend, "_pairing_from_ipc", None),
        pairing_release=getattr(backend, "_release_ipc_pairing", None),
    )
    if not server.start():
        return None
    for event in schema["events"]:
        name = event["name"]
        getattr(backend, name).connect(lambda *args, name=name: server.publish(name, list(args)))
    return server
