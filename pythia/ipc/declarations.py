"""Backend wire declarations independent of the window toolkit."""

from collections.abc import Callable
from threading import Lock
from typing import Any, TypeVar

F = TypeVar("F", bound=Callable)


def method(*params: str, returns: str = "none") -> Callable[[F], F]:
    """Declare an IPC method and its wire argument types."""

    def declare(function: F) -> F:
        setattr(function, "__ipc_method__", (params, returns))  # noqa: B010 -- metadata on generic callable
        return function

    return declare


class EventListeners:
    """An event's listeners for one backend instance."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._callbacks: list[Callable[..., None]] = []

    def connect(self, callback: Callable[..., None]) -> None:
        with self._lock:
            self._callbacks.append(callback)

    def emit(self, *args: Any) -> None:
        with self._lock:
            callbacks = tuple(self._callbacks)
        for callback in callbacks:
            callback(*args)


class Event:
    """Declare event arguments and store listeners on the emitting instance."""

    def __init__(self, *params: str) -> None:
        self.params = params
        self._lock = Lock()

    def __set_name__(self, owner: type, name: str) -> None:
        self._key = f"_ipc_event_{name}"

    def __get__(self, instance: Any, owner: type | None = None):
        if instance is None:
            return self
        with self._lock:
            if self._key not in vars(instance):
                vars(instance)[self._key] = EventListeners()
            return vars(instance)[self._key]
