"""v2.3C-4 deterministic Service/Runtime wiring tests."""

from __future__ import annotations

import asyncio
from dataclasses import MISSING, fields
from pathlib import Path
from typing import Any, cast

import pytest

from agent.runtime_store import SqliteRuntimeStore
from agent.service import (
    AgentService,
    EventStreamRequest,
    EventType,
    RunLookupRequest,
    RunStatus,
    StartRunRequest,
)
from agent.service.runtime_launcher import RuntimeExecutionLauncher


def _start(request_id: str = "service-start") -> StartRunRequest:
    values: dict[str, Any] = {
        "tenant_id": "tenant-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "request_id": request_id,
        "request_text": "生成一个确定性测试结果",
    }
    request_fields = {item.name: item for item in fields(StartRunRequest)}
    if "run_id" in request_fields and request_fields["run_id"].default is MISSING:
        values["run_id"] = "run-service-e2e"
    return StartRunRequest(**cast(Any, values))


def _events_request(run_id: str) -> EventStreamRequest:
    return EventStreamRequest(
        tenant_id="tenant-a",
        user_id="user-a",
        session_id="session-a",
        run_id=run_id,
        request_id="event-read",
        after_sequence=0,
    )


class SuccessfulRuntime:
    calls = 0

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        type(self).calls += 1

    async def run(self, request_text: str) -> str:
        return "ok"

    def close(self) -> None:
        return None


class FailedRuntime(SuccessfulRuntime):
    async def run(self, request_text: str) -> str:
        raise RuntimeError("provider failure must be sanitized")


async def _collect_events(service: AgentService, run_id: str):
    return [event async for event in service.stream_events(_events_request(run_id))]


def test_service_runtime_success_has_consistent_terminal_snapshot_and_events(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        SuccessfulRuntime.calls = 0
        store = SqliteRuntimeStore.open(tmp_path / "success.sqlite")
        service = AgentService(
            runtime_store=store,
            launcher=RuntimeExecutionLauncher(runtime_factory=SuccessfulRuntime),
        )
        try:
            handle = await service.start_run(_start())
            events = await asyncio.wait_for(
                _collect_events(service, handle.run_id),
                timeout=2,
            )
            snapshot = await service.get_run(
                RunLookupRequest(
                    tenant_id="tenant-a",
                    user_id="user-a",
                    session_id="session-a",
                    run_id=handle.run_id,
                    request_id="snapshot",
                )
            )
            assert [event.event_type for event in events] == [
                EventType.RUN_STARTED,
                EventType.RUN_COMPLETED,
            ]
            assert snapshot.status is RunStatus.COMPLETED
            assert events[-1].run_revision == snapshot.revision
            assert SuccessfulRuntime.calls == 1
        finally:
            await service.close()
            if not store.closed:
                store.close()

    asyncio.run(scenario())


def test_service_runtime_failure_has_run_failed_terminal_event(tmp_path: Path) -> None:
    async def scenario() -> None:
        FailedRuntime.calls = 0
        store = SqliteRuntimeStore.open(tmp_path / "failure.sqlite")
        service = AgentService(
            runtime_store=store,
            launcher=RuntimeExecutionLauncher(runtime_factory=FailedRuntime),
        )
        try:
            handle = await service.start_run(_start("service-failure"))
            events = await asyncio.wait_for(
                _collect_events(service, handle.run_id),
                timeout=2,
            )
            snapshot = await service.get_run(
                RunLookupRequest(
                    tenant_id="tenant-a",
                    user_id="user-a",
                    session_id="session-a",
                    run_id=handle.run_id,
                    request_id="snapshot-failure",
                )
            )
            assert [event.event_type for event in events] == [
                EventType.RUN_STARTED,
                EventType.RUN_FAILED,
            ]
            assert snapshot.status is RunStatus.FAILED_TERMINAL
            assert FailedRuntime.calls == 1
        finally:
            await service.close()
            if not store.closed:
                store.close()

    asyncio.run(scenario())


def test_cli_is_only_a_service_adapter() -> None:
    source = Path(__file__).parents[1].joinpath("main.py").read_text()
    assert "AgentService" in source
    assert "from agent.runtime import" not in source
    assert "from agent.orchestrator" not in source
    assert "from agent.event_bus import" not in source
    assert "UniversalAgent" not in source
