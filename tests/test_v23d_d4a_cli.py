from __future__ import annotations

import asyncio
import builtins
import inspect
import sys
import threading
from types import SimpleNamespace

import main as main_module
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
        EventType.RUN_WAITING_USER,
        EventType.RUN_COMPLETED,
    ):
        ServiceCLI._print_event(_event(event_type, 1))

    rendered = capsys.readouterr().out
    assert "已接受取消请求" in rendered
    assert "Run 已取消" in rendered
    assert "Run 已超时" in rendered
    assert "Run 失败" in rendered
    assert "Run 已阻塞" in rendered
    assert "Run 等待用户确认" in rendered
    assert "Run 完成" in rendered


def test_cli_renders_progress_events(capsys) -> None:
    ServiceCLI._print_event(
        RunEvent(
            event_id="event-stage-start",
            sequence_number=1,
            tenant_id="tenant-a",
            session_id="session-a",
            run_id="run-active",
            workflow_id=None,
            stage_id="planning",
            task_id=None,
            event_type=EventType.STAGE_STARTED,
            timestamp="2026-08-11T00:00:00Z",
            payload={"phase": "planning"},
        )
    )
    ServiceCLI._print_event(
        RunEvent(
            event_id="event-task-start",
            sequence_number=2,
            tenant_id="tenant-a",
            session_id="session-a",
            run_id="run-active",
            workflow_id=None,
            stage_id=None,
            task_id=None,
            event_type=EventType.TASK_STARTED,
            timestamp="2026-08-11T00:00:00Z",
            payload={"task_id": "task-1", "executor": "tool"},
        )
    )
    ServiceCLI._print_event(
        RunEvent(
            event_id="event-stage-complete",
            sequence_number=3,
            tenant_id="tenant-a",
            session_id="session-a",
            run_id="run-active",
            workflow_id=None,
            stage_id=None,
            task_id=None,
            event_type=EventType.STAGE_COMPLETED,
            timestamp="2026-08-11T00:00:00Z",
            payload={"phase": "planning"},
        )
    )

    rendered = capsys.readouterr().out
    assert "正在规划任务" in rendered
    assert "开始任务 task-1 (tool)" in rendered
    assert "任务规划完成" in rendered


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


def test_cli_watcher_returns_control_at_waiting_user_event() -> None:
    observed: list[str] = []

    class _EventService:
        def stream_events(self, request):
            async def events():
                yield _event(EventType.RUN_WAITING_USER, 1)
                observed.append("read-past-wait")
                yield _event(EventType.RUN_COMPLETED, 2)

            return events()

    cli = ServiceCLI(
        _EventService(),
        tenant_id="tenant-a",
        user_id="user-a",
        session_id="session-a",
    )

    outcome = asyncio.run(cli._watch_run("run-active"))

    assert outcome is EventType.RUN_WAITING_USER
    assert observed == []


def test_cli_resume_passes_structured_no_effect_confirmation(capsys) -> None:
    class _ResumeService:
        request = None

        async def resume_run(self, request):
            self.request = request
            return SimpleNamespace(run_id=request.run_id)

        def stream_events(self, request):
            async def events():
                yield _event(EventType.RUN_WAITING_USER, 1)

            return events()

        async def get_run(self, request):
            return _snapshot(RunStatus.WAITING_USER)

    service = _ResumeService()
    cli = ServiceCLI(
        service,
        tenant_id="tenant-a",
        user_id="user-a",
        session_id="session-a",
    )
    confirmation = {
        "task_id": "task-7",
        "idempotency_key": "run-active:task-7",
        "external_reference": "payment://txn/18",
        "checked_at": "2026-09-27T10:00:00Z",
    }

    asyncio.run(cli.resume_request(
        "run-active",
        {"no_effect_confirmation": confirmation},
    ))

    assert service.request.run_id == "run-active"
    assert service.request.no_effect_confirmation.to_dict() == confirmation
    assert cli.active_run_id is None
    capsys.readouterr()


def test_cli_main_uses_background_task_and_never_cancels_it() -> None:
    source = inspect.getsource(main)
    assert "asyncio.create_task" in source
    assert "service.cancel_run" not in source
    # Cleaning up the optional repository-enrichment task is unrelated to
    # Run cancellation.  The CLI must not cancel the active Run task itself.
    assert "active_task.cancel()" not in source


def test_cli_eof_waits_for_active_run_and_exits(monkeypatch) -> None:
    inputs = iter(["hello"])
    finished: list[str] = []

    def fake_input(_prompt: str) -> str:
        try:
            return next(inputs)
        except StopIteration as exc:
            raise EOFError from exc

    class _Service:
        async def close(self) -> None:
            return None

    async def fake_run_request(_self, request_text: str) -> None:
        await asyncio.sleep(0.01)
        finished.append(request_text)

    monkeypatch.setattr(builtins, "input", fake_input)
    monkeypatch.setattr(main_module, "load_all", lambda **_kwargs: None)
    monkeypatch.setattr(sys, "argv", ["main.py"])
    monkeypatch.setattr(
        main_module,
        "create_default_agent_service",
        lambda *_args, **_kwargs: _Service(),
    )
    monkeypatch.setattr(main_module.ServiceCLI, "run_request", fake_run_request)

    asyncio.run(asyncio.wait_for(main_module.main(), timeout=1.0))

    assert finished == ["hello"]


def test_cli_queues_request_entered_while_run_is_active(monkeypatch, capsys) -> None:
    inputs = iter(["first", "second"])
    first_run_started = threading.Event()
    run_order: list[str] = []

    def fake_input(_prompt: str) -> str:
        try:
            value = next(inputs)
        except StopIteration as exc:
            raise EOFError from exc
        if value == "second":
            assert first_run_started.wait(timeout=1)
        return value

    class _Service:
        async def close(self) -> None:
            return None

    async def fake_run_request(_self, request_text: str) -> None:
        run_order.append(f"start:{request_text}")
        if request_text == "first":
            first_run_started.set()
            await asyncio.sleep(0.05)
        run_order.append(f"finish:{request_text}")

    monkeypatch.setattr(builtins, "input", fake_input)
    monkeypatch.setattr(main_module, "load_all", lambda **_kwargs: None)
    monkeypatch.setattr(sys, "argv", ["main.py"])
    monkeypatch.setattr(
        main_module,
        "create_default_agent_service",
        lambda *_args, **_kwargs: _Service(),
    )
    monkeypatch.setattr(main_module.ServiceCLI, "run_request", fake_run_request)

    asyncio.run(asyncio.wait_for(main_module.main(), timeout=1.0))

    assert run_order == [
        "start:first",
        "finish:first",
        "start:second",
        "finish:second",
    ]
    assert "输入已排队，完成后自动处理" in capsys.readouterr().out


def test_cli_routes_resume_command_with_json_evidence(monkeypatch) -> None:
    command = (
        '/resume run-active '
        '{"no_effect_confirmation":{"task_id":"task-7",'
        '"idempotency_key":"run-active:task-7",'
        '"external_reference":"payment://txn/18",'
        '"checked_at":"2026-09-27T10:00:00Z"}}'
    )
    inputs = iter([command, "exit"])

    def fake_input(_prompt: str) -> str:
        try:
            return next(inputs)
        except StopIteration as exc:
            raise EOFError from exc

    class _Service:
        resume_request = None

        async def resume_run(self, request):
            self.resume_request = request
            return SimpleNamespace(run_id=request.run_id)

        def stream_events(self, request):
            async def events():
                yield _event(EventType.RUN_WAITING_USER, 1)

            return events()

        async def get_run(self, request):
            return _snapshot(RunStatus.WAITING_USER)

        async def close(self) -> None:
            return None

    service = _Service()
    monkeypatch.setattr(builtins, "input", fake_input)
    monkeypatch.setattr(main_module, "load_all", lambda **_kwargs: None)
    monkeypatch.setattr(sys, "argv", ["main.py"])
    monkeypatch.setattr(
        main_module,
        "create_default_agent_service",
        lambda *_args, **_kwargs: service,
    )

    asyncio.run(asyncio.wait_for(main_module.main(), timeout=1.0))

    assert service.resume_request is not None
    confirmation = service.resume_request.no_effect_confirmation
    assert confirmation.idempotency_key == "run-active:task-7"


def test_cli_run_request_prints_active_run_conflict_immediately(capsys) -> None:
    from agent.service import AgentServiceError, ServiceErrorCode

    class _ActiveRunService:
        async def start_run(self, request):
            raise AgentServiceError(
                ServiceErrorCode.RUN_ALREADY_ACTIVE,
                "another Run is already active in this session",
                details={"active_run_id": "run-orphaned"},
            )

    cli = ServiceCLI(
        _ActiveRunService(),
        tenant_id="tenant-a",
        user_id="user-a",
        session_id="session-a",
    )

    asyncio.run(cli.run_request("继续刚才的任务"))

    out = capsys.readouterr().out
    assert "❌ 无法启动 Run: RUN_ALREADY_ACTIVE" in out
    assert "冲突 Run: run-orphaned" in out
    assert cli.active_run_id is None


def test_cli_resume_request_prints_resume_conflict_immediately(capsys) -> None:
    from agent.service import AgentServiceError, ServiceErrorCode

    class _ActiveRunService:
        async def resume_run(self, request):
            raise AgentServiceError(
                ServiceErrorCode.RUN_ALREADY_ACTIVE,
                "another Run is already active in this session",
            )

    cli = ServiceCLI(
        _ActiveRunService(),
        tenant_id="tenant-a",
        user_id="user-a",
        session_id="session-a",
    )

    asyncio.run(cli.resume_request("run-active"))

    out = capsys.readouterr().out
    assert "❌ 无法恢复 Run: RUN_ALREADY_ACTIVE" in out
    assert "冲突 Run" not in out
