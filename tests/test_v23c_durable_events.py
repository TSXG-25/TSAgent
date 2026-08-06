"""v2.3C-3 durable event stream and cursor replay tests."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from collections.abc import AsyncGenerator
from threading import Barrier
from pathlib import Path
from typing import cast

import pytest

from agent.runtime_store import SCHEMA_VERSION, SqliteRuntimeStore
from agent.service import (
    AgentService,
    AgentServiceError,
    EventStreamRequest,
    EventType,
    PendingRunEvent,
    RunEvent,
    ServiceErrorCode,
    SqliteEventRepository,
)


TENANT = "tenant-a"
SESSION = "session-a"
RUN = "run-events"


class _NoopLauncher:
    async def start(self, **kwargs: object) -> None:
        return None

    async def resume(self, **kwargs: object) -> None:
        return None


def _store(path: Path, *, run_status: str = "RUNNING") -> SqliteRuntimeStore:
    store = SqliteRuntimeStore.open(path)
    store.initialize_run(TENANT, SESSION, RUN, request_id="req-run", run_status=run_status)
    return store


def _event(
    event_id: str,
    *,
    event_type: EventType = EventType.TASK_COMPLETED,
    payload: dict[str, object] | None = None,
    timestamp: str | None = None,
) -> PendingRunEvent:
    return PendingRunEvent(
        event_id=event_id,
        tenant_id=TENANT,
        session_id=SESSION,
        run_id=RUN,
        event_type=event_type,
        timestamp=timestamp or f"2026-08-07T00:00:{event_id[-2:]}Z",
        payload=payload or {"event_id": event_id},
        run_revision=1,
    )


def _request(*, after_sequence: int = 0, limit: int | None = None) -> EventStreamRequest:
    return EventStreamRequest(
        tenant_id=TENANT,
        user_id="user-a",
        session_id=SESSION,
        run_id=RUN,
        request_id="stream-request",
        after_sequence=after_sequence,
        limit=limit,
    )


def test_append_and_exclusive_cursor_read(tmp_path: Path) -> None:
    store = _store(tmp_path / "events.sqlite")
    repository = SqliteEventRepository(store)
    try:
        first = repository.append(_event("event-01"))
        second = repository.append(_event("event-02"))
        third = repository.append(_event("event-03"))

        assert (first.sequence_number, second.sequence_number, third.sequence_number) == (1, 2, 3)
        assert [event.sequence_number for event in repository.read_after(
            tenant_id=TENANT,
            session_id=SESSION,
            run_id=RUN,
            after_sequence=1,
        )] == [2, 3]
        assert repository.latest_sequence(
            tenant_id=TENANT,
            session_id=SESSION,
            run_id=RUN,
        ) == 3
        assert repository.read_after(
            tenant_id=TENANT,
            session_id=SESSION,
            run_id=RUN,
            after_sequence=3,
        ) == ()
        assert repository.read_after(
            tenant_id=TENANT,
            session_id=SESSION,
            run_id=RUN,
            after_sequence=99,
        ) == ()
    finally:
        repository.close()
        store.close()


def test_concurrent_connections_allocate_unique_contiguous_sequences(tmp_path: Path) -> None:
    database = tmp_path / "concurrent.sqlite"
    bootstrap = _store(database)
    bootstrap.close()
    store_a = SqliteRuntimeStore.open(database)
    store_b = SqliteRuntimeStore.open(database)
    barrier = Barrier(2)

    def append(store: SqliteRuntimeStore, event_id: str) -> int:
        repository = SqliteEventRepository(store)
        barrier.wait(timeout=5)
        return repository.append(_event(event_id)).sequence_number

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = (
                executor.submit(append, store_a, "event-a"),
                executor.submit(append, store_b, "event-b"),
            )
            sequences = {future.result(timeout=10) for future in futures}
        assert sequences == {1, 2}
        replay = SqliteEventRepository(store_a).read_after(
            tenant_id=TENANT,
            session_id=SESSION,
            run_id=RUN,
        )
        assert [event.sequence_number for event in replay] == [1, 2]
        assert {event.event_id for event in replay} == {"event-a", "event-b"}
    finally:
        store_a.close()
        store_b.close()


def test_event_id_retry_is_idempotent_and_payload_change_conflicts(tmp_path: Path) -> None:
    store = _store(tmp_path / "idempotency.sqlite")
    repository = SqliteEventRepository(store)
    event = _event("event-same", payload={"value": 1}, timestamp="2026-08-07T00:00:01Z")
    try:
        first = repository.append(event)
        retry = repository.append(event)
        assert retry == first

        with pytest.raises(AgentServiceError) as conflict:
            repository.append(
                _event(
                    "event-same",
                    payload={"value": 2},
                    timestamp="2026-08-07T00:00:01Z",
                )
            )
        assert conflict.value.code.value in {
            "EVENT_IDEMPOTENCY_CONFLICT",
            "IDEMPOTENCY_CONFLICT",
        }
        assert repository.latest_sequence(
            tenant_id=TENANT,
            session_id=SESSION,
            run_id=RUN,
        ) == 1
    finally:
        repository.close()
        store.close()


def test_process_reopen_replays_from_cursor(tmp_path: Path) -> None:
    database = tmp_path / "reopen.sqlite"
    store = _store(database)
    repository = SqliteEventRepository(store)
    repository.append(_event("event-01"))
    repository.append(_event("event-02"))
    repository.close()
    store.close()

    reopened = SqliteRuntimeStore.open(database)
    replay = SqliteEventRepository(reopened).read_after(
        tenant_id=TENANT,
        session_id=SESSION,
        run_id=RUN,
        after_sequence=1,
    )
    try:
        assert [event.event_id for event in replay] == ["event-02"]
        assert reopened.schema_version == SCHEMA_VERSION
    finally:
        reopened.close()


def test_service_default_event_source_replays_after_restart(tmp_path: Path) -> None:
    database = tmp_path / "service-replay.sqlite"
    store_a = _store(database)
    service_a = AgentService(runtime_store=store_a, launcher=_NoopLauncher())
    writer = SqliteEventRepository(store_a)
    writer.append(_event("event-01"))
    writer.append(_event("event-02"))

    async def close_first_service() -> None:
        await service_a.close()

    asyncio.run(close_first_service())
    store_a.close()

    store_b = SqliteRuntimeStore.open(database)
    service_b = AgentService(runtime_store=store_b, launcher=_NoopLauncher())

    async def replay() -> list[RunEvent]:
        return [
            event
            async for event in service_b.stream_events(
                _request(after_sequence=1, limit=1)
            )
        ]

    try:
        assert [event.event_id for event in asyncio.run(replay())] == ["event-02"]
    finally:
        asyncio.run(service_b.close())
        store_b.close()


def test_b_store_migrates_to_c3_event_schema(tmp_path: Path) -> None:
    database = tmp_path / "migration.sqlite"
    old_store = SqliteRuntimeStore.open(database, schema_version="v2.3B-3")
    old_store.initialize_run(TENANT, SESSION, RUN)
    old_store.close()

    store = SqliteRuntimeStore.open(database)
    try:
        repository = SqliteEventRepository(store)
        assert repository.append(_event("event-after-migration")).sequence_number == 1
        assert store.schema_version == SCHEMA_VERSION
    finally:
        store.close()


def test_cursor_floor_is_explicitly_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path / "cursor.sqlite")
    repository = SqliteEventRepository(store)
    try:
        for number in range(1, 4):
            repository.append(_event(f"event-{number:02d}"))
        store.set_event_retention_floor(TENANT, SESSION, RUN, 2)

        with pytest.raises(AgentServiceError) as expired:
            repository.read_after(
                tenant_id=TENANT,
                session_id=SESSION,
                run_id=RUN,
                after_sequence=1,
            )
        assert expired.value.code.value in {
            "EVENT_CURSOR_EXPIRED",
            "CURSOR_INVALID",
            "EVENT_REPLAY_UNAVAILABLE",
        }
        assert [event.sequence_number for event in repository.read_after(
            tenant_id=TENANT,
            session_id=SESSION,
            run_id=RUN,
            after_sequence=2,
        )] == [3]
    finally:
        repository.close()
        store.close()


def test_terminal_stream_yields_terminal_event_then_finishes(tmp_path: Path) -> None:
    store = _store(tmp_path / "terminal.sqlite")
    repository = SqliteEventRepository(store, poll_interval=0.001)
    repository.append(_event("event-start", event_type=EventType.RUN_STARTED))
    repository.append(_event("event-done", event_type=EventType.RUN_COMPLETED))

    async def collect() -> list[RunEvent]:
        return [event async for event in repository.stream(_request())]

    try:
        events = asyncio.run(collect())
        assert [event.event_type for event in events] == [
            EventType.RUN_STARTED,
            EventType.RUN_COMPLETED,
        ]
    finally:
        repository.close()
        store.close()


def test_client_disconnect_does_not_delete_or_stop_replay(tmp_path: Path) -> None:
    store = _store(tmp_path / "disconnect.sqlite")
    repository = SqliteEventRepository(store, poll_interval=0.001)
    repository.append(_event("event-01"))
    repository.append(_event("event-02"))

    async def read_one_and_close() -> None:
        stream = cast(
            AsyncGenerator[RunEvent, None],
            repository.stream(_request(limit=1)),
        )
        events = [event async for event in stream]
        assert [event.sequence_number for event in events] == [1]
        await stream.aclose()

    try:
        asyncio.run(read_one_and_close())
        repository.append(_event("event-03"))
        assert [event.sequence_number for event in repository.read_after(
            tenant_id=TENANT,
            session_id=SESSION,
            run_id=RUN,
            after_sequence=1,
        )] == [2, 3]
    finally:
        repository.close()
        store.close()


def test_cross_tenant_event_lookup_is_not_disclosed(tmp_path: Path) -> None:
    store = _store(tmp_path / "scope.sqlite")
    repository = SqliteEventRepository(store)
    try:
        with pytest.raises(AgentServiceError) as lookup:
            repository.read_after(
                tenant_id="tenant-b",
                session_id="session-b",
                run_id=RUN,
            )
        assert lookup.value.code is ServiceErrorCode.RUN_NOT_FOUND
    finally:
        repository.close()
        store.close()
