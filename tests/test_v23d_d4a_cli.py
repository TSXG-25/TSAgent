from __future__ import annotations

import asyncio
import inspect

from agent.interruption import CancelRunRequest
from agent.service import (
    ArtifactSummary,
    EventType,
    RunEvent,
    RunSnapshot,
    RunStatus,
)
from main import ServiceCLI, main


def _snapshot(
    status: RunStatus,
    *,
    artifacts: tuple[ArtifactSummary, ...] = (),
) -> RunSnapshot:
    return RunSnapshot(
        tenant_id="tenant-a",
        run_id="run-active",
        session_id="session-a",
        status=status,
        request_text="long task",
        active_workflow_id="workflow-a",
        request_id="request-a",
        created_at="2026-08-11T00:00:00Z",
        updated_at="2026-08-11T00:00:01Z",
        artifacts=artifacts,
    )


class _CancelService:
    def __init__(self) -> None:
        self.requests: list[CancelRunRequest] = []

    async def cancel_run(self, request: CancelRunRequest) -> RunSnapshot:
        self.requests.append(request)
        return _snapshot(RunStatus.CANCELLING)


def _event(event_type: EventType, sequence_number: int) -> RunEvent:
    return RunEvent(
        event_id=f"event-{sequence_number}",
        sequence_number=sequence_number,
        tenant_id="tenant-a",
        session_id="session-a",
        run_id="run-active",
        workflow_id=None,
        stage_id=None,
        task_id=None,
        event_type=event_type,
        timestamp="2026-08-11T00:00:00Z",
    )


def test_cli_cancel_uses_active_run_and_only_requests_durable_cancelling(capsys) -> None:
    service = _CancelService()
    cli = ServiceCLI(
        service,
        tenant_id="tenant-a",
        user_id="user-a",
        session_id="session-a",
    )
    cli._active_run_id = "run-active"

    snapshot = asyncio.run(cli.cancel_run())

    assert snapshot is not None
    assert snapshot.status is RunStatus.CANCELLING
    assert len(service.requests) == 1
    request = service.requests[0]
    assert request.run_id == "run-active"
    assert request.tenant_id == "tenant-a"
    assert request.user_id == "user-a"
    assert request.session_id == "session-a"
    assert request.requested_by == "user-a"
    rendered = capsys.readouterr().out
    assert "取消请求已持久化" in rendered
    assert "CANCELLING" in rendered


def test_cli_cancel_accepts_explicit_run_id(capsys) -> None:
    service = _CancelService()
    cli = ServiceCLI(
        service,
        tenant_id="tenant-a",
        user_id="user-a",
        session_id="session-a",
    )
    cli._active_run_id = "run-other"

    asyncio.run(cli.cancel_run("run-explicit"))

    assert service.requests[0].run_id == "run-explicit"
    capsys.readouterr()


def test_cli_event_rendering_is_deterministic(capsys) -> None:
    for event_type in (
        EventType.RUN_CANCELLING,
        EventType.RUN_CANCELLED,
        EventType.RUN_TIMED_OUT,
        EventType.RUN_FAILED,
        EventType.RUN_BLOCKED,
        EventType.RUN_COMPLETED,
    ):
        ServiceCLI._print_event(_event(event_type, 1))

    rendered = capsys.readouterr().out
    assert "已接受取消请求" in rendered
    assert "Run 已取消" in rendered
    assert "Run 已超时" in rendered
    assert "Run 失败" in rendered
    assert "Run 已阻塞" in rendered
    assert "Run 完成" in rendered


def test_cli_renders_preserved_artifacts_after_cancellation(capsys) -> None:
    ServiceCLI._print_snapshot(
        _snapshot(
            RunStatus.CANCELLED,
            artifacts=(
                ArtifactSummary(
                    artifact_id="artifact-report",
                    artifact_type="text/markdown",
                    digest="sha256:report",
                    reference="opaque://artifact-report",
                    exists=True,
                    verified=True,
                    display_name="report.md",
                ),
            ),
        )
    )

    rendered = capsys.readouterr().out
    assert "CANCELLED" in rendered
    assert "已保留完成的产物: report.md" in rendered
    assert "后续任务未执行" in rendered


def test_cli_watcher_consumes_events_until_terminal() -> None:
    class _EventService:
        def stream_events(self, request):
            async def events():
                yield _event(EventType.RUN_CANCELLING, 1)
                yield _event(EventType.RUN_CANCELLED, 2)

            return events()

    cli = ServiceCLI(
        _EventService(),
        tenant_id="tenant-a",
        user_id="user-a",
        session_id="session-a",
    )
    asyncio.run(cli._watch_run("run-active"))


def test_cli_main_uses_background_task_and_never_cancels_it() -> None:
    source = inspect.getsource(main)
    assert "asyncio.create_task" in source
    assert "service.cancel_run" not in source
    # Cleaning up the optional repository-enrichment task is unrelated to
    # Run cancellation.  The CLI must not cancel the active Run task itself.
    assert "active_task.cancel()" not in source
