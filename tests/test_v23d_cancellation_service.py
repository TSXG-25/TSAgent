from __future__ import annotations

import asyncio
import inspect

import pytest

from agent.interruption import (
    CancelRunRequest,
    CancellationCoordinator,
    CancellationIntent,
    InterruptionFailurePoint,
    InterruptionPhase,
    InterruptionReason,
)
from agent.runtime_store import DurableStoreError, SqliteRuntimeStore, StoreErrorCode
from agent.service import (
    AgentService,
    AgentServiceError,
    EventStreamRequest,
    ResumeRunRequest,
    RunLookupRequest,
    RunStatus,
    ServiceErrorCode,
    StartRunRequest,
)
from agent.service.service import AgentService as AgentServiceImplementation


class _BlockingLauncher:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.start_calls = 0
        self.resume_calls = 0

    async def start(self, *, session_context, run_context, request) -> None:
        self.start_calls += 1
        self.started.set()
        await self.release.wait()

    async def resume(self, *, run_context, request) -> None:
        self.resume_calls += 1
        self.started.set()
        await self.release.wait()


def _start() -> StartRunRequest:
    return StartRunRequest(
        tenant_id="tenant-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-service-cancel",
        request_id="start-1",
        request_text="generate a report",
    )


def _lookup(run_id: str, *, tenant_id: str = "tenant-a") -> RunLookupRequest:
    return RunLookupRequest(
        tenant_id=tenant_id,
        user_id="user-a",
        session_id="session-a",
        run_id=run_id,
        request_id="lookup-1",
    )


def _cancel(run_id: str, *, request_id: str = "cancel-1", tenant_id: str = "tenant-a") -> CancelRunRequest:
    return CancelRunRequest(
        tenant_id=tenant_id,
        user_id="user-a",
        session_id="session-a",
        run_id=run_id,
        request_id=request_id,
        requested_by="user-a",
    )


def test_service_cancel_is_fast_durable_acceptance_not_false_cancelled(tmp_path) -> None:
    async def scenario() -> None:
        store = SqliteRuntimeStore.open(tmp_path / "cancel.sqlite")
        launcher = _BlockingLauncher()
        service = AgentService(runtime_store=store, launcher=launcher)
        try:
            handle = await service.start_run(_start())
            await asyncio.wait_for(launcher.started.wait(), timeout=1)

            snapshot = await asyncio.wait_for(
                service.cancel_run(_cancel(handle.run_id)),
                timeout=1,
            )
            assert snapshot.status is RunStatus.CANCELLING
            assert launcher.start_calls == 1
            events = store.read_events(
                "tenant-a", handle.run_id, session_id="session-a"
            )
            assert [event.event_type for event in events].count("run_cancelling") == 1
            assert all(event.event_type != "run_cancelled" for event in events)

            duplicate = await service.cancel_run(_cancel(handle.run_id))
            assert duplicate.status is RunStatus.CANCELLING
            assert len(store.read_events(
                "tenant-a", handle.run_id, session_id="session-a"
            )) == len(events)
        finally:
            launcher.release.set()
            await service.close()

    asyncio.run(scenario())


def test_safe_boundary_terminalizes_once_and_snapshot_matches_event(tmp_path) -> None:
    async def scenario() -> None:
        store = SqliteRuntimeStore.open(tmp_path / "terminal.sqlite")
        launcher = _BlockingLauncher()
        service = AgentService(runtime_store=store, launcher=launcher)
        try:
            handle = await service.start_run(_start())
            await asyncio.wait_for(launcher.started.wait(), timeout=1)
            await service.cancel_run(_cancel(handle.run_id))
            fence = store.get_current_fence(
                "tenant-a", handle.run_id, session_id="session-a"
            )
            assert fence is not None
            coordinator = CancellationCoordinator(store)
            first = coordinator.mark_safe_to_interrupt(
                tenant_id="tenant-a",
                session_id="session-a",
                run_id=handle.run_id,
                request_id="cancel-1",
                writer_id=fence.writer_id,
                fence_token=fence.fence_token,
            )
            second = coordinator.mark_safe_to_interrupt(
                tenant_id="tenant-a",
                session_id="session-a",
                run_id=handle.run_id,
                request_id="cancel-1",
                writer_id=fence.writer_id,
                fence_token=fence.fence_token,
            )
            assert first.intent.phase is InterruptionPhase.FINALIZED
            assert second.idempotent is True
            snapshot = await service.get_run(_lookup(handle.run_id))
            assert snapshot.status is RunStatus.CANCELLED
            events = store.read_events(
                "tenant-a", handle.run_id, session_id="session-a"
            )
            assert [event.event_type for event in events].count("run_cancelled") == 1
            assert events[-1].event_type == "run_cancelled"
        finally:
            launcher.release.set()
            await service.close()

    asyncio.run(scenario())


def test_cancelling_survives_restart_and_resume_is_rejected(tmp_path) -> None:
    async def scenario() -> None:
        database = tmp_path / "restart.sqlite"
        store_a = SqliteRuntimeStore.open(database)
        launcher = _BlockingLauncher()
        service_a = AgentService(runtime_store=store_a, launcher=launcher)
        handle = await service_a.start_run(_start())
        await asyncio.wait_for(launcher.started.wait(), timeout=1)
        await service_a.cancel_run(_cancel(handle.run_id))
        launcher.release.set()
        await service_a.close()

        store_b = SqliteRuntimeStore.open(database)
        service_b = AgentService(runtime_store=store_b, launcher=_BlockingLauncher())
        try:
            snapshot = await service_b.get_run(_lookup(handle.run_id))
            assert snapshot.status is RunStatus.CANCELLING
            intent = store_b.get_interruption(
                "tenant-a", handle.run_id, session_id="session-a"
            )
            assert intent is not None
            with pytest.raises(AgentServiceError) as caught:
                await service_b.resume_run(
                    ResumeRunRequest(
                        tenant_id="tenant-a",
                        user_id="user-a",
                        session_id="session-a",
                        run_id=handle.run_id,
                        request_id="resume-1",
                    )
                )
            assert caught.value.code is ServiceErrorCode.RUN_ALREADY_CANCELLING
        finally:
            await service_b.close()

    asyncio.run(scenario())


def test_run_timeout_primitive_is_terminal_and_not_resumable(tmp_path) -> None:
    async def scenario() -> None:
        database = tmp_path / "timeout.sqlite"
        store = SqliteRuntimeStore.open(database)
        store.initialize_run(
            "tenant-a", "session-a", "run-timeout", "start-timeout"
        )
        fence = store.acquire_fence(
            "tenant-a", "session-a", "run-timeout", writer_id="worker-a"
        )
        coordinator = CancellationCoordinator(
            store, clock=lambda: "2026-08-10T00:00:00Z"
        )
        record = coordinator.request_run_timeout(
            tenant_id="tenant-a",
            user_id="user-a",
            session_id="session-a",
            run_id="run-timeout",
            request_id="timeout-1",
            requested_by="runtime-deadline",
        )
        assert record.intent.reason is InterruptionReason.RUN_TIMEOUT
        coordinator.mark_safe_to_interrupt(
            tenant_id="tenant-a",
            session_id="session-a",
            run_id="run-timeout",
            request_id="timeout-1",
            writer_id=fence.writer_id,
            fence_token=fence.fence_token,
        )
        head = store.get_run_head("tenant-a", "run-timeout")
        events = store.read_events(
            "tenant-a", "run-timeout", session_id="session-a"
        )
        assert head is not None and head.run_status == "TIMED_OUT"
        assert events[-1].event_type == "run_timed_out"
        store.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "failure_point",
    [
        InterruptionFailurePoint.AFTER_PHASE_UPDATE,
        InterruptionFailurePoint.AFTER_REVISION_INSERT,
        InterruptionFailurePoint.AFTER_EVENT_APPEND,
        InterruptionFailurePoint.AFTER_HEAD_UPDATE,
        InterruptionFailurePoint.BEFORE_COMMIT,
    ],
)
def test_terminalization_fault_never_tears_snapshot_and_event(tmp_path, failure_point) -> None:
    store = SqliteRuntimeStore.open(tmp_path / f"fault-{failure_point.value}.sqlite")
    store.initialize_run("tenant-a", "session-a", "run-1", "start-1")
    fence = store.acquire_fence(
        "tenant-a", "session-a", "run-1", writer_id="worker-a"
    )
    intent = CancellationIntent(
        tenant_id="tenant-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-1",
        request_id="cancel-1",
        requested_at="2026-08-10T00:00:00Z",
        requested_by="user-a",
        reason=InterruptionReason.USER_CANCEL,
        revision=0,
    )
    store.request_interruption(intent, request_digest=intent.intent_digest)
    store.advance_interruption_phase(
        "tenant-a",
        "session-a",
        "run-1",
        request_id="cancel-1",
        target_phase=InterruptionPhase.OBSERVED,
        writer_id=fence.writer_id,
        fence_token=fence.fence_token,
    )
    before_head = store.get_run_head("tenant-a", "run-1")
    before_events = store.read_events(
        "tenant-a", "run-1", session_id="session-a"
    )
    with pytest.raises(DurableStoreError) as caught:
        store.finalize_interruption(
            "tenant-a",
            "session-a",
            "run-1",
            request_id="cancel-1",
            writer_id=fence.writer_id,
            fence_token=fence.fence_token,
            failure_point=failure_point,
        )
    assert caught.value.code is StoreErrorCode.INTERRUPTION_INJECTED_FAILURE
    assert store.get_run_head("tenant-a", "run-1") == before_head
    assert store.read_events(
        "tenant-a", "run-1", session_id="session-a"
    ) == before_events
    assert store.get_interruption("tenant-a", "run-1").intent.phase is InterruptionPhase.OBSERVED
    store.close()


def test_cross_tenant_cancel_is_not_disclosed(tmp_path) -> None:
    async def scenario() -> None:
        store = SqliteRuntimeStore.open(tmp_path / "scope.sqlite")
        launcher = _BlockingLauncher()
        service = AgentService(runtime_store=store, launcher=launcher)
        try:
            handle = await service.start_run(_start())
            await asyncio.wait_for(launcher.started.wait(), timeout=1)
            with pytest.raises(AgentServiceError) as caught:
                await service.cancel_run(_cancel(handle.run_id, tenant_id="tenant-b"))
            assert caught.value.code is ServiceErrorCode.RUN_NOT_FOUND
            assert store.get_interruption("tenant-a", handle.run_id) is None
        finally:
            launcher.release.set()
            await service.close()

    asyncio.run(scenario())


def test_runtime_completion_cannot_overwrite_cancelling(tmp_path) -> None:
    store = SqliteRuntimeStore.open(tmp_path / "race.sqlite")
    store.initialize_run("tenant-a", "session-a", "run-1", "start-1")
    fence = store.acquire_fence(
        "tenant-a", "session-a", "run-1", writer_id="worker-a"
    )
    intent = CancellationIntent(
        tenant_id="tenant-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-1",
        request_id="cancel-1",
        requested_at="2026-08-10T00:00:00Z",
        requested_by="user-a",
        reason=InterruptionReason.USER_CANCEL,
        revision=0,
    )
    store.request_interruption(intent, request_digest=intent.intent_digest)
    with pytest.raises(DurableStoreError) as caught:
        store.transition_run_with_event(
            "tenant-a",
            "session-a",
            "run-1",
            run_status="COMPLETED",
            event_id="run-completed:run-1:start-1",
            event_type="run_completed",
            timestamp="2026-08-10T00:00:01Z",
            payload={},
            writer_id=fence.writer_id,
            fence_token=fence.fence_token,
            request_id="start-1",
        )
    assert caught.value.code is StoreErrorCode.REVISION_CONFLICT
    assert store.get_run_head("tenant-a", "run-1").run_status == "CANCELLING"
    assert all(
        event.event_type != "run_completed"
        for event in store.read_events(
            "tenant-a", "run-1", session_id="session-a"
        )
    )
    store.close()


def test_completed_run_rejects_cancel_without_creating_intent(tmp_path) -> None:
    async def scenario() -> None:
        store = SqliteRuntimeStore.open(tmp_path / "completed.sqlite")
        launcher = _BlockingLauncher()
        service = AgentService(runtime_store=store, launcher=launcher)
        try:
            handle = await service.start_run(_start())
            await asyncio.wait_for(launcher.started.wait(), timeout=1)
            fence = store.get_current_fence(
                "tenant-a", handle.run_id, session_id="session-a"
            )
            assert fence is not None
            store.transition_run_with_event(
                "tenant-a",
                "session-a",
                handle.run_id,
                run_status="RUNNING",
                event_id="run-started:completed-fixture",
                event_type="run_started",
                timestamp="2026-08-10T00:00:00Z",
                payload={},
                writer_id=fence.writer_id,
                fence_token=fence.fence_token,
                request_id="start-1",
            )
            store.transition_run_with_event(
                "tenant-a",
                "session-a",
                handle.run_id,
                run_status="COMPLETED",
                event_id="run-completed:completed-fixture",
                event_type="run_completed",
                timestamp="2026-08-10T00:00:01Z",
                payload={},
                writer_id=fence.writer_id,
                fence_token=fence.fence_token,
                request_id="start-1",
            )
            with pytest.raises(AgentServiceError) as caught:
                await service.cancel_run(_cancel(handle.run_id))
            assert caught.value.code is ServiceErrorCode.RUN_NOT_CANCELLABLE
            assert store.get_interruption("tenant-a", handle.run_id) is None
        finally:
            launcher.release.set()
            await service.close()

    asyncio.run(scenario())


def test_cancelled_run_rejects_new_cancel_and_resume(tmp_path) -> None:
    async def scenario() -> None:
        store = SqliteRuntimeStore.open(tmp_path / "already-cancelled.sqlite")
        launcher = _BlockingLauncher()
        service = AgentService(runtime_store=store, launcher=launcher)
        try:
            handle = await service.start_run(_start())
            await asyncio.wait_for(launcher.started.wait(), timeout=1)
            await service.cancel_run(_cancel(handle.run_id))
            fence = store.get_current_fence(
                "tenant-a", handle.run_id, session_id="session-a"
            )
            assert fence is not None
            CancellationCoordinator(store).mark_safe_to_interrupt(
                tenant_id="tenant-a",
                session_id="session-a",
                run_id=handle.run_id,
                request_id="cancel-1",
                writer_id=fence.writer_id,
                fence_token=fence.fence_token,
            )
            with pytest.raises(AgentServiceError) as duplicate:
                await service.cancel_run(_cancel(handle.run_id, request_id="cancel-2"))
            assert duplicate.value.code is ServiceErrorCode.ALREADY_CANCELLED
            with pytest.raises(AgentServiceError) as resume:
                await service.resume_run(
                    ResumeRunRequest(
                        tenant_id="tenant-a",
                        user_id="user-a",
                        session_id="session-a",
                        run_id=handle.run_id,
                        request_id="resume-after-cancel",
                    )
                )
            assert resume.value.code is ServiceErrorCode.RESUME_NOT_ALLOWED
        finally:
            launcher.release.set()
            await service.close()

    asyncio.run(scenario())


def test_client_disconnect_does_not_lose_terminal_event_on_restart(tmp_path) -> None:
    async def scenario() -> None:
        database = tmp_path / "event-restart.sqlite"
        store_a = SqliteRuntimeStore.open(database)
        launcher = _BlockingLauncher()
        service_a = AgentService(runtime_store=store_a, launcher=launcher)
        handle = await service_a.start_run(_start())
        await asyncio.wait_for(launcher.started.wait(), timeout=1)
        await service_a.cancel_run(_cancel(handle.run_id))

        stream = service_a.stream_events(
            EventStreamRequest(
                tenant_id="tenant-a",
                user_id="user-a",
                session_id="session-a",
                run_id=handle.run_id,
                request_id="stream-1",
                after_sequence=0,
            )
        )
        first = await anext(stream)
        assert first.sequence_number == 1
        await stream.aclose()

        fence = store_a.get_current_fence(
            "tenant-a", handle.run_id, session_id="session-a"
        )
        assert fence is not None
        CancellationCoordinator(store_a).mark_safe_to_interrupt(
            tenant_id="tenant-a",
            session_id="session-a",
            run_id=handle.run_id,
            request_id="cancel-1",
            writer_id=fence.writer_id,
            fence_token=fence.fence_token,
        )
        launcher.release.set()
        await service_a.close()

        store_b = SqliteRuntimeStore.open(database)
        service_b = AgentService(runtime_store=store_b, launcher=_BlockingLauncher())
        try:
            replay = service_b.stream_events(
                EventStreamRequest(
                    tenant_id="tenant-a",
                    user_id="user-a",
                    session_id="session-a",
                    run_id=handle.run_id,
                    request_id="stream-2",
                    after_sequence=first.sequence_number,
                )
            )
            events = [event async for event in replay]
            assert [event.event_type.value for event in events] == [
                "run_cancelling",
                "run_cancelled",
            ]
            assert events[-1].is_terminal
            assert (await service_b.get_run(_lookup(handle.run_id))).status is RunStatus.CANCELLED
        finally:
            await service_b.close()

    asyncio.run(scenario())


def test_service_cancel_delegates_to_coordinator_not_store_sql() -> None:
    source = inspect.getsource(AgentServiceImplementation.cancel_run)
    assert "self._cancellation.request_cancel" in source
    assert "request_interruption(" not in source
    assert "connection.execute" not in source
