"""The server of the backend channel: authenticates a connection, runs its calls, pushes events.

Messages (all framing.encode'd JSON objects):

    client -> server   {"id": 7, "method": "get_play_state", "args": ["steam:440"]}
                       `args` is a list, or an object keyed by the schema's parameter names; omitted means none.
    server -> client   {"id": 7, "result": <a backend contract answer>}
                       when that answer is {"status": "pending", "request": "x-3"}, the settled answer follows later:
                       {"id": 7, "settled": true, "result": {"status": "ok", ..., "request": "x-3"}}
                       a signal of the Backend:  {"event": "gamesChanged", "args": []}

The first message of a connection is `hello` with the install's key (pythia.instance.channel_key), as a hex string
in `args[0]` or `args.token`; nothing else is answered before it. Built-in methods: `hello`, `ping`, `schema` (the
schema this server runs from) and `subscribe` (a list of event names, or null for all, the default).

A call is made as the HTML UI's is: through `call_on_main`, which the running Pythia points at the Qt thread, so a
slot behaves the same whoever calls it. Whatever a slot returns or raises becomes a contract answer (rule 1 of
contract/README.md): the caller never sees a raw failure. Every connection has its own writer thread and a bounded
queue, so a slow client cannot block the threads that emit signals; one that falls too far behind is dropped.
"""

import contextlib
import hmac
import queue
import threading
from collections import OrderedDict
from collections.abc import Callable, Mapping
from typing import Any

from pythia.ipc import PROTOCOL, framing, transport, ui_contract
from pythia.util.log import logger

BUILTINS = ("hello", "ping", "schema", "subscribe")
HELLO_TIMEOUT_S = 10.0
OUTBOX_LIMIT = 2000
PAIRING_METHODS = frozenset(("start_pair_offer", "show_pairing_code", "stop_pair_offer", "cancel_pairing"))
_EARLY_LIMIT = 256
_TYPE_CHECKS: dict[str, Callable[[Any], bool]] = {
    "string": lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "any": lambda v: True,
}


def to_answer(slot: str, value: Any) -> dict[str, Any]:
    """What a slot returned, as a contract answer: an answer is kept, a bare list (the legacy slots) or string
    is the `value` of an ok, nothing is a bare ok, and anything else is an error."""
    if value is None:
        return ui_contract.ok()
    if isinstance(value, (str, int, float, bool, list)):
        return ui_contract.ok(value=value)
    # The old Look page uses status for its UI phase, not the answer envelope.
    # Preserve those phases as data without weakening validation for other slots.
    if slot == "get_look" and isinstance(value, dict) and value.get("status") in ("none", "looking"):
        fields = {key: item for key, item in value.items() if key != "status"}
        return ui_contract.ok(slot=slot, state=value["status"], **fields)
    decoded = ui_contract.decode(value)
    if decoded.get("code") == "malformed":
        logger.warning("The slot %s answered with something that is not a contract answer.", slot)
        return ui_contract.error(ui_contract.MALFORMED, slot=slot, code="malformed")
    return value


class _Connection:
    def __init__(self, server: "Server", stream: transport.Stream):
        self.server = server
        self.stream = stream
        self.authenticated = False
        self.events: set[str] | None = None  # None: every event
        self._outbox: queue.Queue = queue.Queue(OUTBOX_LIMIT)
        self._closed = threading.Event()
        self.pairing_requested = False

    def start(self) -> None:
        threading.Thread(target=self._write_loop, name="pythia-ipc-write", daemon=True).start()
        threading.Thread(target=self._read_loop, name="pythia-ipc-read", daemon=True).start()
        timer = threading.Timer(HELLO_TIMEOUT_S, self._hello_expired)
        timer.daemon = True
        timer.start()

    def _hello_expired(self) -> None:
        if not self.authenticated:
            self.close()

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    def send(self, message: dict[str, Any]) -> bool:
        """Queues a message; False (and the connection ends) when it cannot be delivered."""
        if self.closed:
            return False
        try:
            data = framing.encode(message)
            self._outbox.put_nowait(data)
        except (queue.Full, framing.FrameError):
            self.close()
            return False
        return True

    def end(self) -> None:
        """Closes once what was queued has been sent (a refusal is read by the client before it is hung up on)."""
        try:
            self._outbox.put_nowait(None)
        except queue.Full:
            self.close()

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        with contextlib.suppress(queue.Full):
            self._outbox.put_nowait(None)
        self.stream.close()
        self.server._forget(self)  # noqa: SLF001

    def _write_loop(self) -> None:
        while True:
            data = self._outbox.get()
            if data is None:
                self.close()
                return
            try:
                self.stream.send(data)
            except (OSError, EOFError, ValueError):
                self.close()
                return

    def _read_loop(self) -> None:
        decoder = framing.FrameDecoder()
        try:
            while not self.closed:
                data = self.stream.recv()
                if not data:
                    break
                for message in decoder.feed(data):
                    self.server._handle(self, message)  # noqa: SLF001
        except framing.FrameError as ex:
            logger.debug("A backend connection sent a bad frame: %s", ex)
        finally:
            self.close()


class Server:
    """`schema` is pythia.ipc.schema's; `handlers` maps each of its method names to the callable to run;
    `key` is the install's key."""

    def __init__(
        self,
        schema: Mapping[str, Any],
        handlers: Mapping[str, Callable[..., Any]],
        key: bytes,
        call_on_main: Callable[[Callable[[], Any]], Any] | None = None,
        where: str | None = None,
        pairing_call: Callable[[str, object, Callable[[], bool]], Any] | None = None,
        pairing_release: Callable[[object], None] | None = None,
    ):
        self._schema = schema
        self._methods = {m["name"]: m for m in schema["methods"]}
        self._events = {e["name"] for e in schema["events"]}
        clash = set(self._methods) & set(BUILTINS)
        if clash:
            raise ValueError(f"Backend slots named like built-in methods: {sorted(clash)}")
        missing = set(self._methods) - set(handlers)
        if missing:
            raise ValueError(f"No handler for {sorted(missing)}")
        self._handlers = handlers
        self._pairing_call = pairing_call
        self._pairing_release = pairing_release
        self._key = key
        self._call_on_main = call_on_main or (lambda fn: fn())
        self._where = where
        self._listener: transport.Listener | None = None
        self._lock = threading.Lock()
        self._connections: set[_Connection] = set()
        self._pending: dict[str, tuple[_Connection, Any]] = {}
        self._early: OrderedDict[str, dict[str, Any]] = OrderedDict()

    # ---- lifecycle ---------------------------------------------------------------
    def start(self) -> bool:
        """False when another Pythia already serves this channel (this one then does not)."""
        try:
            self._listener = transport.listen(self._where)
        except transport.AddressInUse:
            return False
        threading.Thread(target=self._accept_loop, name="pythia-ipc-accept", daemon=True).start()
        return True

    def stop(self) -> None:
        listener, self._listener = self._listener, None
        if listener is not None:
            listener.close()
        with self._lock:
            connections = list(self._connections)
        for connection in connections:
            connection.close()

    def _accept_loop(self) -> None:
        while True:
            listener = self._listener
            if listener is None:
                return
            try:
                stream = listener.accept()
            except Exception as ex:  # noqa: BLE001 -- keep serving unless this server was stopped
                if self._listener is None:
                    return
                logger.debug("The backend channel could not accept a connection: %s", ex)
                continue
            connection = _Connection(self, stream)
            with self._lock:
                self._connections.add(connection)
            connection.start()

    def _forget(self, connection: _Connection) -> None:
        with self._lock:
            self._connections.discard(connection)
            for request in [r for r, (c, _) in self._pending.items() if c is connection]:
                del self._pending[request]
        if connection.pairing_requested and self._pairing_release is not None:
            try:
                self._pairing_release(connection)
            except Exception:  # noqa: BLE001 -- one cleanup failure must not leave the other connections open
                logger.exception("The disconnected UI's pairing cleanup could not be scheduled")

    # ---- one message -------------------------------------------------------------
    def _handle(self, connection: _Connection, message: dict[str, Any]) -> None:
        call_id = message.get("id")
        method = message.get("method")
        valid_id = isinstance(call_id, (int, str)) and not isinstance(call_id, bool)
        if not isinstance(method, str) or not valid_id:
            connection.send(
                self._reply(
                    call_id if valid_id else None,
                    "",
                    "bad_request",
                    'A call is {"id": number or text, "method": text, "args": list or object}.',
                )
            )
            return
        if not connection.authenticated:
            if method != "hello":
                connection.send(self._reply(call_id, method, "unauthenticated", "Say hello with the key first."))
                connection.end()
                return
            self._hello(connection, call_id, message.get("args"))
            return
        if method in BUILTINS:
            connection.send({"id": call_id, "result": self._builtin(connection, method, message.get("args"))})
            return
        spec = self._methods.get(method)
        if spec is None:
            connection.send(self._reply(call_id, method, "unknown_method", f"Pythia has no method '{method}'."))
            return
        args = self._arguments(spec, message.get("args"))
        if isinstance(args, str):
            connection.send(self._reply(call_id, method, "bad_args", args))
            return
        if method in PAIRING_METHODS and self._pairing_call is not None:
            connection.pairing_requested = True
        answer = self._call_on_main(lambda: self._invoke(method, args, connection))
        connection.send({"id": call_id, "result": answer})
        if answer.get("status") == "pending":
            self._expect(connection, call_id, answer["request"])

    @staticmethod
    def _reply(call_id: Any, slot: str, code: str, message: str) -> dict[str, Any]:
        return {"id": call_id, "result": ui_contract.error(message, slot=slot or None, code=code)}

    def _hello(self, connection: _Connection, call_id: Any, args: Any) -> None:
        token = args.get("token") if isinstance(args, dict) else (args[0] if isinstance(args, list) and args else None)
        try:
            given = bytes.fromhex(token) if isinstance(token, str) else b""
        except ValueError:
            given = b""
        if not given or not hmac.compare_digest(given, self._key):
            connection.send(self._reply(call_id, "hello", "unauthorized", "That is not this Pythia's key."))
            connection.end()
            return
        connection.authenticated = True
        connection.send(
            {
                "id": call_id,
                "result": ui_contract.ok(
                    "hello", server="pythia", protocol=PROTOCOL, methods=len(self._methods), events=len(self._events)
                ),
            }
        )

    def _builtin(self, connection: _Connection, method: str, args: Any) -> dict[str, Any]:
        if method == "ping":
            return ui_contract.ok("ping")
        if method == "hello":
            return ui_contract.ok("hello", server="pythia", protocol=PROTOCOL)
        if method == "schema":
            return ui_contract.ok("schema", schema=self._schema)
        names = args[0] if isinstance(args, list) and args else (args.get("events") if isinstance(args, dict) else None)
        if names is None:
            connection.events = None
        elif isinstance(names, list) and all(isinstance(n, str) and n in self._events for n in names):
            connection.events = set(names)
        else:
            return ui_contract.error("subscribe takes a list of event names, or null.", "subscribe", "bad_args")
        return ui_contract.ok("subscribe")

    def _arguments(self, spec: Mapping[str, Any], args: Any) -> list[Any] | str:
        """The argument list for a call, or the reason it cannot be made."""
        params = spec["params"]
        if args is None:
            args = []
        if isinstance(args, dict):
            names = [p["name"] for p in params]
            unknown = [k for k in args if k not in names]
            if unknown:
                return f"{spec['name']} has no argument {unknown[0]}."
            ordered: list[Any] = []
            for param in params:
                if param["name"] in args:
                    ordered.append(args[param["name"]])
                elif param.get("optional"):
                    break
                else:
                    return f"{spec['name']} needs {param['name']}."
            args = ordered
        if not isinstance(args, list):
            return "args is a list or an object."
        required = sum(1 for p in params if not p.get("optional"))
        if not required <= len(args) <= len(params):
            return f"{spec['name']} takes {required} to {len(params)} arguments, not {len(args)}."
        for param, value in zip(params, args):
            if not _TYPE_CHECKS[param["type"]](value):
                return f"{spec['name']}: {param['name']} is a {param['type']}."
        return args

    def _invoke(self, name: str, args: list[Any], connection: _Connection | None = None) -> dict[str, Any]:
        """Runs the slot; always an answer. Arguments are not logged (set_account_credential carries a secret)."""

        def failed(slot: str, exc: Exception) -> None:
            logger.error("%s failed over the backend channel: %s", slot, exc, exc_info=exc)

        def invoke():
            if name in PAIRING_METHODS and self._pairing_call is not None and connection is not None:
                return self._pairing_call(name, connection, lambda: not connection.closed)
            return self._handlers[name](*args)

        return ui_contract.guarded(name, lambda: to_answer(name, invoke()), log=failed)

    # ---- pending answers and events ----------------------------------------------
    def _expect(self, connection: _Connection, call_id: Any, request: str) -> None:
        """A pending answer was sent: its settlement goes to this connection, which may already have arrived."""
        with self._lock:
            early = self._early.pop(request, None)
            if early is None:
                self._pending[request] = (connection, call_id)
        if early is not None:
            connection.send({"id": call_id, "settled": True, "result": early})

    def publish(self, event: str, args: list[Any]) -> None:
        """A signal of the Backend, from any thread."""
        if event == "asyncResult":
            self._settle(args[0] if args and isinstance(args[0], dict) else {})
            return
        if event not in self._events:
            return
        with self._lock:
            targets = [c for c in self._connections if c.authenticated and (c.events is None or event in c.events)]
        message = {"event": event, "args": args}
        for connection in targets:
            connection.send(message)

    def _settle(self, answer: dict[str, Any]) -> None:
        request = answer.get("request")
        if not isinstance(request, str):
            return
        answer = to_answer("asyncResult", answer)
        with self._lock:
            owner = self._pending.pop(request, None)
            if owner is None:
                # Not (yet) a call of ours: settled before its pending answer was sent, or a call of another UI.
                self._early[request] = answer
                while len(self._early) > _EARLY_LIMIT:
                    self._early.popitem(last=False)
                return
        connection, call_id = owner
        connection.send({"id": call_id, "settled": True, "result": answer})
