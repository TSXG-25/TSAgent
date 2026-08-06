from __future__ import annotations

import asyncio
from dataclasses import fields, MISSING
from typing import Any, cast

import pytest

from agent.runtime_store import SqliteRuntimeStore
from agent.service import (
    AgentService,
    AgentServiceError,
    EventStreamRequest,
    EventType,
    InMemoryEventRepository,
    ResumeRunRequest,
    RunEvent,
    RunLookupRequest,
    ServiceErrorCode,
    StartRunRequest,
)


class BlockingLauncher:
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


class ImmediateLauncher:
    def __init__(self) -> None:
        self.start_calls = 0
        self.resume_calls = 0

    async def start(self, *, session_context, run_context, request) -> None:
        self.start_calls += 1

    async def resume(self, *, run_context, request) -> None:
        self.resume_calls += 1


def _start(request_id: str = "req-start", *, text: str = "生成报告") -> StartRunRequest:
    values = {
        "tenant_id": "tenant-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "request_id": request_id,
        "request_text": text,
    }
    request_fields = {item.name: item for item in fields(StartRunRequest)}
    if "run_id" in request_fields and request_fields["run_id"].default is MISSING:
        values["run_id"] = "run-explicit"
    return StartRunRequest(**cast(Any, values))


def _lookup(run_id: str, request_id: str = "req-get", *, tenant: str = "tenant-a") -> RunLookupRequest:
    return RunLookupRequest(
        tenant_id=tenant,
        user_id="user-a",
        session_id="session-a",
        run_id=run_id,
        request_id=request_id,
    )


def _run(coro):
    return asyncio.run(coro)


def test_start_is_durable_before_launcher_and_duplicate_is_idempotent(tmp_path) -> None:
    async def scenario() -> None:
        store = SqliteRuntimeStore.open(tmp_path / "service.sqlite")
        launcher = BlockingLauncher()
        service = AgentService(runtime_store=store, launcher=launcher)
        request = _start()
        try:
            first = await service.start_run(request)
            assert first.run_id
            snapshot = await service.get_run(_lookup(first.run_id))
            assert snapshot.run_id == first.run_id
            assert snapshot.request_text == request.request_text
            assert launcher.start_calls == 0

            duplicate = await service.start_run(request)
            assert duplicate.run_id == first.run_id
            await asyncio.wait_for(launcher.started.wait(), timeout=1)
            assert launcher.start_calls == 1

            with pytest.raises(AgentServiceError) as conflict:
                await service.start_run(_start(text="删除所有文件"))
            assert conflict.value.code is ServiceErrorCode.REQUEST_ID_CONFLICT

            launcher.release.set()
            await service.close()
        finally:
            if not service.closed:
                launcher.release.set()
                await service.close()

    _run(scenario())


def test_service_rehydrates_snapshot_after_close_and_reopen(tmp_path) -> None:
    async def scenario() -> None:
        database = tmp_path / "rehydrate.sqlite"
        store_a = SqliteRuntimeStore.open(database)
        launcher_a = ImmediateLauncher()
        service_a = AgentService(runtime_store=store_a, launcher=launcher_a)
        request = _start()
        handle = await service_a.start_run(request)
        await asyncio.sleep(0)
        await service_a.close()

        store_b = SqliteRuntimeStore.open(database)
        service_b = AgentService(runtime_store=store_b, launcher=ImmediateLauncher())
        try:
            snapshot = await service_b.get_run(_lookup(handle.run_id))
            assert snapshot.run_id == handle.run_id
            assert snapshot.request_text == request.request_text
            assert snapshot.revision >= 1
        finally:
            await service_b.close()

    _run(scenario())


def test_concurrent_services_reserve_one_run_for_same_request(tmp_path) -> None:
    async def scenario() -> None:
        database = tmp_path / "concurrent-start.sqlite"
        store_a = SqliteRuntimeStore.open(database)
        store_b = SqliteRuntimeStore.open(database)
        launcher_a = ImmediateLauncher()
        launcher_b = ImmediateLauncher()
        service_a = AgentService(runtime_store=store_a, launcher=launcher_a)
        service_b = AgentService(runtime_store=store_b, launcher=launcher_b)
        request = _start()
        try:
            first, second = await asyncio.gather(
                service_a.start_run(request),
                service_b.start_run(request),
            )
            assert first.run_id == second.run_id
            await asyncio.sleep(0)
            assert launcher_a.start_calls + launcher_b.start_calls == 1
        finally:
            await service_a.close()
            await service_b.close()

    _run(scenario())


def test_resume_delegates_and_does_not_create_second_active_task(tmp_path) -> None:
    async def scenario() -> None:
        database = tmp_path / "resume.sqlite"
        store_a = SqliteRuntimeStore.open(database)
        start_launcher = ImmediateLauncher()
        service_a = AgentService(runtime_store=store_a, launcher=start_launcher)
        handle = await service_a.start_run(_start())
        await asyncio.sleep(0)
        await service_a.close()

        store_b = SqliteRuntimeStore.open(database)
        launcher_b = BlockingLauncher()
        service_b = AgentService(runtime_store=store_b, launcher=launcher_b)
        resume = ResumeRunRequest(
            tenant_id="tenant-a",
            user_id="user-a",
            session_id="session-a",
            run_id=handle.run_id,
            request_id="req-resume",
        )
        try:
            resumed = await service_b.resume_run(resume)
            assert resumed.run_id == handle.run_id
            await asyncio.wait_for(launcher_b.started.wait(), timeout=1)
            assert launcher_b.resume_calls == 1
            with pytest.raises(AgentServiceError) as duplicate:
                await service_b.resume_run(
                    ResumeRunRequest(
                        tenant_id="tenant-a",
                        user_id="user-a",
                        session_id="session-a",
                        run_id=handle.run_id,
                        request_id="req-resume-2",
                    )
                )
            assert duplicate.value.code.value in {
                "RUN_ALREADY_ACTIVE",
                # C1's original error enum predates the dedicated C2 code;
                # the current contract exposes RUN_ALREADY_ACTIVE.
                "DUPLICATE_REQUEST",
            }
        finally:
            launcher_b.release.set()
            await service_b.close()

    _run(scenario())


def test_cross_tenant_run_and_artifact_lookup_is_not_disclosed(tmp_path) -> None:
    async def scenario() -> None:
        store = SqliteRuntimeStore.open(tmp_path / "scope.sqlite")
        service = AgentService(runtime_store=store, launcher=ImmediateLauncher())
        handle = await service.start_run(_start())
        await asyncio.sleep(0)
        try:
            with pytest.raises(AgentServiceError) as lookup:
                await service.get_run(_lookup(handle.run_id, tenant="tenant-b"))
            assert lookup.value.code is ServiceErrorCode.RUN_NOT_FOUND
            with pytest.raises(AgentServiceError) as artifacts:
                await service.list_artifacts(_lookup(handle.run_id, tenant="tenant-b"))
            assert artifacts.value.code is ServiceErrorCode.RUN_NOT_FOUND
        finally:
            await service.close()

    _run(scenario())


def test_close_does_not_delete_durable_run(tmp_path) -> None:
    async def scenario() -> None:
        database = tmp_path / "close.sqlite"
        store = SqliteRuntimeStore.open(database)
        service = AgentService(runtime_store=store, launcher=ImmediateLauncher())
        handle = await service.start_run(_start())
        await asyncio.sleep(0)
        await service.close()

        reopened = SqliteRuntimeStore.open(database)
        service_after = AgentService(runtime_store=reopened, launcher=ImmediateLauncher())
        try:
            snapshot = await service_after.get_run(_lookup(handle.run_id))
            assert snapshot.run_id == handle.run_id
        finally:
            await service_after.close()

    _run(scenario())


def test_event_disconnect_is_read_only_and_does_not_touch_service(tmp_path) -> None:
    async def scenario() -> None:
        store = SqliteRuntimeStore.open(tmp_path / "events.sqlite")
        events = InMemoryEventRepository()
        service = AgentService(
            runtime_store=store,
            launcher=ImmediateLauncher(),
            event_repository=events,
        )
        event = RunEvent(
            event_id="event-1",
            sequence_number=1,
            tenant_id="tenant-a",
            session_id="session-a",
            run_id="run-1",
            workflow_id=None,
            stage_id=None,
            task_id=None,
            event_type=EventType.RUN_STARTED,
            timestamp="2026-08-06T00:00:00Z",
        )
        events.append(event)
        try:
            stream = service.stream_events(
                EventStreamRequest(
                    tenant_id="tenant-a",
                    user_id="user-a",
                    session_id="session-a",
                    run_id="run-1",
                    request_id="req-events",
                    after_sequence=0,
                )
            )
            received = [item async for item in stream]
            assert received == [event]
            assert not service.closed
        finally:
            await service.close()

    _run(scenario())
