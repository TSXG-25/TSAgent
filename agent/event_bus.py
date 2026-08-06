import asyncio
import inspect
import threading
from typing import Any, Awaitable, Callable, Dict, Optional


class EventScopeClosedError(RuntimeError):
    """Raised when a closed event scope is used."""


async def _await_callback(result: Awaitable[Any]) -> None:
    await result


class Subscription:
    """Idempotent handle for one EventBus subscription."""

    def __init__(self, bus: "EventBus", event_type: object, token: int) -> None:
        self._bus = bus
        self._event_type = event_type
        self._token = token
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        if self._closed:
            return
        self._bus.unsubscribe(self._event_type, self._token)
        self._closed = True

    def __enter__(self) -> "Subscription":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

class EventBus:
    """Lifecycle-scoped in-process event bus.

    Each Runtime Context owns its own instance. The module-level ``event_bus``
    below remains a compatibility bus for legacy workspace/diagnostic paths.
    """

    def __init__(self, *, scope_id: str = "") -> None:
        self.scope_id = str(scope_id or "")
        self._subscribers: Dict[object, Dict[int, Callable[[Any], Any]]] = {}
        self._next_token = 0
        self._closed = False
        self._lock = threading.RLock()

    @property
    def closed(self) -> bool:
        return self._closed

    def _ensure_open(self) -> None:
        if self._closed:
            raise EventScopeClosedError(
                f"event bus is closed: {self.scope_id or '<unnamed>'}"
            )

    def subscribe(self, event_type: object, callback: Callable[[Any], Any]) -> Subscription:
        self._ensure_open()
        if not callable(callback):
            raise TypeError("event callback must be callable")
        with self._lock:
            self._next_token += 1
            token = self._next_token
            self._subscribers.setdefault(event_type, {})[token] = callback
        return Subscription(self, event_type, token)

    def unsubscribe(self, event_type: object, token: int) -> None:
        with self._lock:
            subscribers = self._subscribers.get(event_type)
            if not subscribers:
                return
            subscribers.pop(token, None)
            if not subscribers:
                self._subscribers.pop(event_type, None)

    def subscriber_count(self, event_type: Optional[object] = None) -> int:
        with self._lock:
            if event_type is not None:
                return len(self._subscribers.get(event_type, {}))
            return sum(len(callbacks) for callbacks in self._subscribers.values())

    def emit(self, event_type: object, data: Any) -> int:
        with self._lock:
            self._ensure_open()
            callbacks = list(self._subscribers.get(event_type, {}).values())
        for callback in callbacks:
            result = callback(data)
            if inspect.isawaitable(result):
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    asyncio.run(_await_callback(result))
                else:
                    loop.create_task(_await_callback(result))
        return len(callbacks)

    def close(self) -> None:
        """Close the scope and release all subscriber references."""
        with self._lock:
            if self._closed:
                return
            self._subscribers.clear()
            self._closed = True


event_bus = EventBus(scope_id="legacy")


__all__ = [
    "EventBus",
    "EventScopeClosedError",
    "Subscription",
    "event_bus",
]
