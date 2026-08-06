"""C-2 in-process EventSource boundary.

Durable event persistence and cursor replay across process restart are C-3.
This module provides an explicit repository protocol and a deterministic
in-memory adapter for Service Core tests.
"""

from __future__ import annotations

import threading
from collections.abc import AsyncIterator

from .contracts import EventStreamRequest, RunEvent
from .errors import AgentServiceError, ServiceErrorCode
from .events import EventOrderingOracle


class EventRepository:
    def stream(self, request: EventStreamRequest) -> AsyncIterator[RunEvent]:
        raise NotImplementedError

    def close(self) -> None:
        return None


async def _empty_stream() -> AsyncIterator[RunEvent]:
    if False:  # pragma: no cover - makes this an async generator
        yield RunEvent  # type: ignore[misc]


class EmptyEventRepository(EventRepository):
    def stream(self, request: EventStreamRequest) -> AsyncIterator[RunEvent]:
        return _empty_stream()


class InMemoryEventRepository(EventRepository):
    """Process-local test source; it is intentionally not a C-3 store."""

    def __init__(self) -> None:
        self._events: dict[tuple[str, str], list[RunEvent]] = {}
        self._lock = threading.RLock()
        self._closed = False

    def append(self, event: RunEvent) -> None:
        with self._lock:
            if self._closed:
                raise AgentServiceError(
                    ServiceErrorCode.SERVICE_CLOSED,
                    "event repository is closed",
                )
            key = (event.tenant_id, event.run_id)
            current = self._events.setdefault(key, [])
            candidate = tuple((*current, event))
            EventOrderingOracle.validate(
                candidate,
                tenant_id=event.tenant_id,
                session_id=event.session_id,
                run_id=event.run_id,
            )
            current.append(event)

    def stream(self, request: EventStreamRequest) -> AsyncIterator[RunEvent]:
        with self._lock:
            if self._closed:
                raise AgentServiceError(
                    ServiceErrorCode.SERVICE_CLOSED,
                    "event repository is closed",
                )
            events = tuple(
                self._events.get((request.tenant_id, request.run_id), ())
            )
        replay = EventOrderingOracle.replay_after(
            events,
            request.after_sequence,
            tenant_id=request.tenant_id,
            session_id=request.session_id,
            run_id=request.run_id,
        )
        if request.limit is not None:
            replay = replay[: request.limit]

        async def _stream() -> AsyncIterator[RunEvent]:
            for event in replay:
                yield event

        return _stream()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._events.clear()


__all__ = [
    "EmptyEventRepository",
    "EventRepository",
    "InMemoryEventRepository",
]
