"""Event repository adapters for the AgentService boundary.

The in-memory adapter remains useful for C-2 unit tests.  C-3 adds the
SQLite-backed adapter whose event rows, sequence allocation and cursor floor
survive Service and process restart.
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import Any

from agent.runtime_store import DurableStoreError, SqliteRuntimeStore, StoreErrorCode

from .contracts import EventStreamRequest, EventType, RunEvent
from .errors import AgentServiceError, ServiceErrorCode
from .events import EventOrderingOracle


@dataclass(frozen=True)
class PendingRunEvent:
    """Event input whose sequence is assigned by the durable Store."""

    event_id: str
    tenant_id: str
    session_id: str
    run_id: str
    event_type: EventType | str
    timestamp: str
    workflow_id: str | None = None
    stage_id: str | None = None
    task_id: str | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)
    run_revision: int = 0


def _service_code(name: str, fallback: ServiceErrorCode) -> ServiceErrorCode:
    """Use newer C-3 error names while remaining readable on the C-1 enum."""

    value = getattr(ServiceErrorCode, name, None)
    return value if isinstance(value, ServiceErrorCode) else fallback


def _map_store_error(error: DurableStoreError) -> AgentServiceError:
    if error.code in {StoreErrorCode.RUN_NOT_FOUND, StoreErrorCode.IDENTITY_MISMATCH}:
        code = ServiceErrorCode.RUN_NOT_FOUND
        message = "Run was not found in the requested scope"
    elif error.code is StoreErrorCode.EVENT_CURSOR_EXPIRED:
        code = _service_code(
            "EVENT_CURSOR_EXPIRED", ServiceErrorCode.EVENT_REPLAY_UNAVAILABLE
        )
        message = "event cursor is no longer readable"
    elif error.code is StoreErrorCode.IDEMPOTENCY_CONFLICT:
        code = _service_code(
            "EVENT_IDEMPOTENCY_CONFLICT", ServiceErrorCode.REQUEST_ID_CONFLICT
        )
        message = "event_id is bound to a different event"
    elif error.code is StoreErrorCode.STORE_CLOSED:
        code = ServiceErrorCode.SERVICE_CLOSED
        message = "event repository is closed"
    else:
        code = _service_code("INTERNAL_ERROR", ServiceErrorCode.INVALID_REQUEST)
        message = "durable event operation failed"
    return AgentServiceError(code, message)


class EventRepository:
    def append(self, event: PendingRunEvent | RunEvent) -> RunEvent:
        raise NotImplementedError

    def read_after(
        self,
        *,
        tenant_id: str,
        session_id: str,
        run_id: str,
        after_sequence: int = 0,
        limit: int | None = None,
    ) -> tuple[RunEvent, ...]:
        raise NotImplementedError

    def latest_sequence(
        self, *, tenant_id: str, session_id: str, run_id: str
    ) -> int:
        raise NotImplementedError

    def stream(self, request: EventStreamRequest) -> AsyncIterator[RunEvent]:
        raise NotImplementedError

    def close(self) -> None:
        return None


async def _empty_stream() -> AsyncIterator[RunEvent]:
    if False:  # pragma: no cover - makes this an async generator
        yield RunEvent  # type: ignore[misc]


class EmptyEventRepository(EventRepository):
    def append(self, event: PendingRunEvent | RunEvent) -> RunEvent:
        raise AgentServiceError(
            ServiceErrorCode.UNSUPPORTED_OPERATION,
            "the empty event repository cannot append events",
        )

    def stream(self, request: EventStreamRequest) -> AsyncIterator[RunEvent]:
        return _empty_stream()


class SqliteEventRepository(EventRepository):
    """Durable per-Run event repository backed by ``SqliteRuntimeStore``."""

    def __init__(
        self,
        store: SqliteRuntimeStore,
        *,
        poll_interval: float = 0.05,
        max_batch_size: int = 100,
    ) -> None:
        if poll_interval <= 0:
            raise ValueError("poll_interval must be positive")
        if max_batch_size < 1:
            raise ValueError("max_batch_size must be positive")
        self._store = store
        self._poll_interval = poll_interval
        self._max_batch_size = max_batch_size
        self._closed = False

    @staticmethod
    def _to_public(record: Any) -> RunEvent:
        try:
            payload = json.loads(record.payload_json)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise AgentServiceError(
                _service_code("INTERNAL_ERROR", ServiceErrorCode.INVALID_REQUEST),
                "durable event payload is not valid JSON",
            ) from error
        if not isinstance(payload, dict):
            raise AgentServiceError(
                _service_code("INTERNAL_ERROR", ServiceErrorCode.INVALID_REQUEST),
                "durable event payload must be a JSON object",
            )
        return RunEvent(
            event_id=record.event_id,
            sequence_number=record.sequence_number,
            tenant_id=record.tenant_id,
            session_id=record.session_id,
            run_id=record.run_id,
            workflow_id=record.workflow_id,
            stage_id=record.stage_id,
            task_id=record.task_id,
            event_type=EventType(record.event_type),
            timestamp=record.timestamp,
            payload=payload,
            run_revision=record.run_revision,
        )

    def _ensure_open(self) -> None:
        if self._closed:
            raise AgentServiceError(
                ServiceErrorCode.SERVICE_CLOSED,
                "event repository is closed",
            )

    def append(self, event: PendingRunEvent | RunEvent) -> RunEvent:
        self._ensure_open()
        pending = (
            event
            if isinstance(event, PendingRunEvent)
            else PendingRunEvent(
                event_id=event.event_id,
                tenant_id=event.tenant_id,
                session_id=event.session_id,
                run_id=event.run_id,
                event_type=event.event_type,
                timestamp=event.timestamp,
                workflow_id=event.workflow_id,
                stage_id=event.stage_id,
                task_id=event.task_id,
                payload=event.payload,
                run_revision=event.run_revision,
            )
        )
        try:
            record = self._store.append_event(
                pending.tenant_id,
                pending.session_id,
                pending.run_id,
                event_id=pending.event_id,
                event_type=pending.event_type,
                timestamp=pending.timestamp,
                payload=pending.payload,
                workflow_id=pending.workflow_id,
                stage_id=pending.stage_id,
                task_id=pending.task_id,
                run_revision=pending.run_revision,
            )
            return self._to_public(record)
        except DurableStoreError as error:
            raise _map_store_error(error) from error

    def read_after(
        self,
        *,
        tenant_id: str,
        session_id: str,
        run_id: str,
        after_sequence: int = 0,
        limit: int | None = None,
    ) -> tuple[RunEvent, ...]:
        self._ensure_open()
        try:
            records = self._store.read_events(
                tenant_id,
                run_id,
                session_id=session_id,
                after_sequence=after_sequence,
                limit=limit,
            )
            return tuple(self._to_public(record) for record in records)
        except DurableStoreError as error:
            raise _map_store_error(error) from error

    def latest_sequence(
        self, *, tenant_id: str, session_id: str, run_id: str
    ) -> int:
        self._ensure_open()
        try:
            return self._store.get_event_head(
                tenant_id,
                run_id,
                session_id=session_id,
            ).latest_sequence
        except DurableStoreError as error:
            raise _map_store_error(error) from error

    def stream(self, request: EventStreamRequest) -> AsyncIterator[RunEvent]:
        self._ensure_open()

        async def _stream() -> AsyncIterator[RunEvent]:
            cursor = request.after_sequence
            yielded = 0
            while not self._closed:
                remaining = (
                    None
                    if request.limit is None
                    else request.limit - yielded
                )
                if remaining == 0:
                    return
                batch_limit = self._max_batch_size
                if remaining is not None:
                    batch_limit = min(batch_limit, remaining)
                events = self.read_after(
                    tenant_id=request.tenant_id,
                    session_id=request.session_id,
                    run_id=request.run_id,
                    after_sequence=cursor,
                    limit=batch_limit,
                )
                if events:
                    for event in events:
                        cursor = event.sequence_number
                        yielded += 1
                        yield event
                        if event.is_terminal:
                            return
                        if request.limit is not None and yielded >= request.limit:
                            return
                    continue

                try:
                    event_head = self._store.get_event_head(
                        request.tenant_id,
                        request.run_id,
                        session_id=request.session_id,
                    )
                except DurableStoreError as error:
                    raise _map_store_error(error) from error
                if (
                    event_head.terminal_sequence is not None
                    and event_head.terminal_sequence <= cursor
                ):
                    return
                await asyncio.sleep(self._poll_interval)

        return _stream()

    def close(self) -> None:
        self._closed = True


class InMemoryEventRepository(EventRepository):
    """Process-local test source; it is intentionally not a C-3 store."""

    def __init__(self) -> None:
        self._events: dict[tuple[str, str], list[RunEvent]] = {}
        self._lock = threading.RLock()
        self._closed = False

    def append(self, event: PendingRunEvent | RunEvent) -> RunEvent:
        with self._lock:
            if self._closed:
                raise AgentServiceError(
                    ServiceErrorCode.SERVICE_CLOSED,
                    "event repository is closed",
                )
            if isinstance(event, PendingRunEvent):
                event = RunEvent(
                    event_id=event.event_id,
                    sequence_number=len(self._events.get((event.tenant_id, event.run_id), ())) + 1,
                    tenant_id=event.tenant_id,
                    session_id=event.session_id,
                    run_id=event.run_id,
                    workflow_id=event.workflow_id,
                    stage_id=event.stage_id,
                    task_id=event.task_id,
                    event_type=EventType(event.event_type),
                    timestamp=event.timestamp,
                    payload=event.payload,
                    run_revision=event.run_revision,
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
            return event

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
    "PendingRunEvent",
    "SqliteEventRepository",
]
