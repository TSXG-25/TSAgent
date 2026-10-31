"""v2.3C-4 deterministic Service/Runtime wiring tests."""

from __future__ import annotations

import asyncio
from dataclasses import MISSING, fields, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

import pytest

from agent.runtime_store import SqliteRuntimeStore
from agent.registry.tool_registry import ToolAvailability, ToolRegistry
from agent.service.context_factory import ServiceContextFactory
from agent.service import (
    AgentService,
    AgentServiceError,
    EventStreamRequest,
    EventType,
    ResumeRunRequest,
    RunLookupRequest,
    RunStatus,
    ServiceErrorCode,
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


class ProgressRuntime(SuccessfulRuntime):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.run_context = kwargs["run_context"]

    async def run(self, request_text: str) -> str:
        bus = self.run_context.event_bus
        bus.emit(
            "stage_started",
            {"stage_id": "planning", "phase": "planning"},
        )
        bus.emit(
            "task_started",
            {"task_id": "task-1", "verb": "read", "executor": "tool"},
        )
        bus.emit(
            "task_completed",
            {
                "task_id": "task-1",
                "verb": "read",
                "executor": "tool",
                "status": "succeeded",
                "verified": True,
                "error_code": "",
            },
        )
        bus.emit(
            "stage_completed",
            {"stage_id": "planning", "phase": "planning", "task_count": 1},
        )
        return "ok"


class FailedRuntime(SuccessfulRuntime):
    async def run(self, request_text: str) -> str:
        raise RuntimeError("provider failure must be sanitized")


class BudgetExhaustedRuntime(SuccessfulRuntime):
    """The coroutine returns normally, but Runtime evidence says it failed."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.last_run_evidence = {
            "terminal_status": "FAILED_TERMINAL",
            "terminal_outputs_verified": False,
            "budget_exhausted": True,
            "runtime_pending": True,
            "task_failures": [],
            "failure_code": "RUNTIME_BUDGET_EXHAUSTED",
        }


async def _collect_events(service: AgentService, run_id: str):
    return [event async for event in service.stream_events(_events_request(run_id))]


async def _collect_events_for_scope(
    service: AgentService,
    *,
    tenant_id: str,
    user_id: str,
    session_id: str,
    run_id: str,
):
    request = EventStreamRequest(
        tenant_id=tenant_id,
        user_id=user_id,
        session_id=session_id,
        run_id=run_id,
        request_id="event-read-scoped",
        after_sequence=0,
    )
    return [event async for event in service.stream_events(request)]


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
                EventType.RUN_CREATED,
                EventType.RUN_STARTED,
                EventType.RUN_COMPLETED,
            ]
            assert snapshot.status is RunStatus.COMPLETED
            assert events[-1].run_revision == snapshot.revision

            next_handle = await service.start_run(_start("sequential-session-run"))
            next_events = await asyncio.wait_for(
                _collect_events(service, next_handle.run_id),
                timeout=2,
            )
            assert next_events[-1].event_type is EventType.RUN_COMPLETED
            assert next_handle.run_id != handle.run_id
            assert SuccessfulRuntime.calls == 2
        finally:
            await service.close()
            if not store.closed:
                store.close()

    asyncio.run(scenario())


def test_service_lease_prevents_second_process_takeover_of_active_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        import agent.service.service as service_module

        monkeypatch.setattr(service_module, "WORKER_LEASE_SECONDS", 0.03)
        database = tmp_path / "active-worker-lease.sqlite"
        store_a = SqliteRuntimeStore.open(database)
        store_b = None

        class WaitingRuntime:
            started = asyncio.Event()
            release = asyncio.Event()

            def __init__(self, *_args: Any, **_kwargs: Any) -> None:
                pass

            async def run(self, _request_text: str) -> str:
                type(self).started.set()
                await type(self).release.wait()
                return "ok"

            def close(self) -> None:
                pass

        service_a = AgentService(
            runtime_store=store_a,
            launcher=RuntimeExecutionLauncher(runtime_factory=WaitingRuntime),
        )
        service_b = None
        try:
            handle = await service_a.start_run(_start("active-worker-start"))
            await asyncio.wait_for(WaitingRuntime.started.wait(), timeout=2)
            managed_task = service_a._runs[handle.run_id].task
            lease_before = store_a._connection.execute(
                "SELECT heartbeat_at FROM run_worker_leases WHERE run_id = ?",
                (handle.run_id,),
            ).fetchone()["heartbeat_at"]
            await asyncio.sleep(0.05)
            lease_after = store_a._connection.execute(
                "SELECT heartbeat_at FROM run_worker_leases WHERE run_id = ?",
                (handle.run_id,),
            ).fetchone()["heartbeat_at"]
            assert lease_after > lease_before

            store_b = SqliteRuntimeStore.open(database)
            service_b = AgentService(
                runtime_store=store_b,
                launcher=RuntimeExecutionLauncher(runtime_factory=SuccessfulRuntime),
            )
            with pytest.raises(AgentServiceError) as session_active:
                await service_b.start_run(_start("second-active-session-run"))
            assert session_active.value.code is ServiceErrorCode.RUN_ALREADY_ACTIVE
            assert session_active.value.details["active_run_id"] == handle.run_id
            assert store_b._connection.execute(
                "SELECT COUNT(*) FROM run_heads WHERE tenant_id = ? AND session_id = ?",
                ("tenant-a", "session-a"),
            ).fetchone()[0] == 1

            with pytest.raises(AgentServiceError) as active:
                await service_b.resume_run(ResumeRunRequest(
                    tenant_id="tenant-a",
                    user_id="user-a",
                    session_id="session-a",
                    run_id=handle.run_id,
                    request_id="active-worker-resume",
                ))
            assert active.value.code is ServiceErrorCode.RUN_ALREADY_ACTIVE

            WaitingRuntime.release.set()
            await asyncio.wait_for(managed_task, timeout=2)
        finally:
            WaitingRuntime.release.set()
            if service_b is not None:
                await service_b.close()
            await service_a.close()
            if not store_a.closed:
                store_a.close()
            if store_b is not None and not store_b.closed:
                store_b.close()

    asyncio.run(scenario())


def test_service_runtime_progress_events_are_durable_and_ordered(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        store = SqliteRuntimeStore.open(tmp_path / "progress.sqlite")
        service = AgentService(
            runtime_store=store,
            launcher=RuntimeExecutionLauncher(runtime_factory=ProgressRuntime),
        )
        try:
            handle = await service.start_run(_start("service-progress"))
            events = await asyncio.wait_for(
                _collect_events(service, handle.run_id),
                timeout=2,
            )
            assert [event.event_type for event in events] == [
                EventType.RUN_CREATED,
                EventType.RUN_STARTED,
                EventType.STAGE_STARTED,
                EventType.TASK_STARTED,
                EventType.TASK_COMPLETED,
                EventType.STAGE_COMPLETED,
                EventType.RUN_COMPLETED,
            ]
            assert events[2].payload["phase"] == "planning"
            assert events[3].task_id == "task-1"
            assert events[3].payload["task_id"] == "task-1"
            assert events[4].payload["verified"] is True
            assert events[-1].is_terminal is True
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
                EventType.RUN_CREATED,
                EventType.RUN_STARTED,
                EventType.RUN_FAILED,
            ]
            assert snapshot.status is RunStatus.FAILED_TERMINAL
            assert snapshot.failure_summary is not None
            assert snapshot.failure_summary.code == "RUNTIME_EXCEPTION"
            assert FailedRuntime.calls == 1
            with pytest.raises(AgentServiceError) as rejected:
                await service.resume_run(
                    ResumeRunRequest(
                        tenant_id="tenant-a",
                        user_id="user-a",
                        session_id="session-a",
                        run_id=handle.run_id,
                        request_id="resume-terminal-failure",
                    )
                )
            assert rejected.value.code is ServiceErrorCode.RESUME_NOT_ALLOWED
        finally:
            await service.close()
            if not store.closed:
                store.close()

    asyncio.run(scenario())


def test_budget_exhaustion_cannot_commit_run_completed(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = SqliteRuntimeStore.open(tmp_path / "budget.sqlite")
        service = AgentService(
            runtime_store=store,
            launcher=RuntimeExecutionLauncher(runtime_factory=BudgetExhaustedRuntime),
        )
        try:
            handle = await service.start_run(_start("service-budget"))
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
                    request_id="snapshot-budget",
                )
            )
            event_types = [event.event_type for event in events]
            assert EventType.RUN_COMPLETED not in event_types
            assert event_types[-1] is EventType.RUN_FAILED
            assert snapshot.status is RunStatus.FAILED_TERMINAL
            assert snapshot.failure_summary is not None
            assert snapshot.failure_summary.code == "RUNTIME_BUDGET_EXHAUSTED"
            assert snapshot.failure_summary.retryable is False
        finally:
            await service.close()
            if not store.closed:
                store.close()

    asyncio.run(scenario())


def test_service_entry_blocks_fresh_research_when_search_tool_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        from agent.cognition.execution_need import RequestedOutcome
        from agent.cognition.intent_schema import DOMAIN_WEB, IntentResult
        from agent.orchestrator import planner as planner_module
        from agent.services.memory_service import ScopedMemoryView

        class IntentEngine:
            async def analyze_async(self, _context):
                return IntentResult(
                    domain=DOMAIN_WEB,
                    action="fresh_research",
                    requires_execution=True,
                    summary="fresh research",
                    raw_input="查一下今天的 AI 新闻",
                    resolved_request="查一下今天的 AI 新闻",
                    freshness_required=True,
                    source_grounding_required=True,
                    requested_outcomes=(RequestedOutcome.USER_VISIBLE_OUTPUT,),
                )

        registry = ToolRegistry()

        async def web_search(query: str) -> str:
            return query

        registry.register(
            web_search,
            name="web_search",
            category="web",
            tags=["search", "web"],
            availability=lambda: ToolAvailability(
                available=False,
                reason_code="RESEARCH_TOOL_UNAVAILABLE",
                reason="duckduckgo-mcp-server is not installed",
            ),
        )
        monkeypatch.setattr(planner_module, "intent_engine", IntentEngine())
        monkeypatch.setattr(planner_module, "_tool_registry", registry)

        store = SqliteRuntimeStore.open(tmp_path / "search-unavailable.sqlite")
        service = AgentService(
            runtime_store=store,
            launcher=RuntimeExecutionLauncher(),
            context_factory=ServiceContextFactory(
                store,
                workspace_root=tmp_path,
            ),
        )
        request = StartRunRequest(
            tenant_id="availability-tenant",
            user_id="availability-user",
            session_id="availability-session",
            request_id="availability-start",
            request_text="查一下今天的 AI 新闻",
        )
        try:
            handle = await service.start_run(request)
            events = await asyncio.wait_for(
                _collect_events_for_scope(
                    service,
                    tenant_id=request.tenant_id,
                    user_id=request.user_id,
                    session_id=request.session_id,
                    run_id=handle.run_id,
                ),
                timeout=5,
            )
            snapshot = await service.get_run(
                RunLookupRequest(
                    tenant_id=request.tenant_id,
                    user_id=request.user_id,
                    session_id=request.session_id,
                    run_id=handle.run_id,
                    request_id="availability-lookup",
                )
            )

            assert events[-1].event_type is EventType.RUN_BLOCKED
            assert snapshot.status is RunStatus.BLOCKED
            assert snapshot.failure_summary is not None
            assert snapshot.failure_summary.code == "RESEARCH_TOOL_UNAVAILABLE"
            assert (
                snapshot.failure_summary.details["failure_detail"]
                == "duckduckgo-mcp-server is not installed"
            )
            assert snapshot.output is not None
            assert "duckduckgo-mcp-server is not installed" in snapshot.output.text
        finally:
            await service.close()

    asyncio.run(scenario())


def test_cli_is_only_a_service_adapter() -> None:
    source = Path(__file__).parents[1].joinpath("main.py").read_text()
    assert "AgentService" in source
    assert "from agent.runtime import" not in source
    assert "from agent.orchestrator" not in source
    assert "from agent.event_bus import" not in source
    assert "from agent.runtime_store" not in source
    assert "runtime_launcher" not in source
    assert "UniversalAgent" not in source


def test_client_disconnect_does_not_stop_runtime_and_reconnect_replays_events(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        database = tmp_path / "disconnect-reconnect.sqlite"
        store_a = SqliteRuntimeStore.open(database)
        service_a = AgentService(
            runtime_store=store_a,
            launcher=RuntimeExecutionLauncher(runtime_factory=SuccessfulRuntime),
        )
        try:
            handle = await service_a.start_run(_start("disconnect-reconnect"))
            stream = service_a.stream_events(_events_request(handle.run_id))
            first = await stream.__anext__()
            assert first.event_type is EventType.RUN_CREATED
            # The client stops reading here.  Runtime execution must not be
            # cancelled or restarted by the abandoned read iterator.
            del stream
            remaining = await asyncio.wait_for(
                _collect_events(service_a, handle.run_id),
                timeout=2,
            )
            assert remaining[-1].event_type is EventType.RUN_COMPLETED
        finally:
            await service_a.close()
            if not store_a.closed:
                store_a.close()

        store_b = SqliteRuntimeStore.open(database)
        service_b = AgentService(
            runtime_store=store_b,
            launcher=RuntimeExecutionLauncher(runtime_factory=SuccessfulRuntime),
        )
        try:
            replay = [
                event
                async for event in service_b.stream_events(
                    EventStreamRequest(
                        tenant_id="tenant-a",
                        user_id="user-a",
                        session_id="session-a",
                        run_id=handle.run_id,
                        request_id="reconnect-events",
                        after_sequence=1,
                    )
                )
            ]
            assert [event.event_type for event in replay] == [
                EventType.RUN_STARTED,
                EventType.RUN_COMPLETED,
            ]
        finally:
            await service_b.close()

    asyncio.run(scenario())



def test_service_restart_resumes_checkpoint_without_relaunching_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        from agent.executor.executors.workflow import WorkflowExecutor
        from agent.checkpoint import (
            ExternalStateGuard,
            SideEffectState,
            TaskEffectRecord,
        )
        from agent.registry.workflow_registry import workflow_registry
        from agent.run_resume import RunResumeCoordinator
        from agent.run_resume.execution_context import (
            new_single_workflow_index,
            workflow_execution_context,
        )
        from agent.workflow import (
            ExecutionResult,
            ExecutionSpec,
            ExecutorType,
            OutputArtifact,
            Stage,
            ToolArgument,
            Workflow,
        )
        from agent.service import (
            ExternalStateEvidence,
            NoEffectConfirmation as ServiceNoEffectConfirmation,
            ResumeRunRequest,
        )

        database = tmp_path / "checkpoint-resume.sqlite"
        workflow = Workflow(
            id="service_resume_fixture",
            version="1.0",
            description="resume fixture",
            stages=[Stage(
                id="emit",
                description="emit deterministic result",
                execution=ExecutionSpec(executor=ExecutorType.LLM),
                arguments=[
                    ToolArgument(param="path", constant="output/resume.txt"),
                    ToolArgument(param="content", constant="resume-result"),
                ],
                outputs=[OutputArtifact(type="answer")],
            )],
        )
        original_execute = WorkflowExecutor.execute
        original_initial_checkpoint = RunResumeCoordinator._initial_activation_checkpoint
        checkpoint_guard = ExternalStateGuard(
            resource_id="object:resume-fixture",
            guard_type="OBJECT_VERSION",
            expected_value="v1",
            checked_at="2026-09-26T00:00:00Z",
        )
        interrupted = False
        stage_contexts = []

        def initial_checkpoint_with_guard(self, index, selected_workflow, attempt_id):
            checkpoint = original_initial_checkpoint(
                self,
                index,
                selected_workflow,
                attempt_id,
            )
            idempotency_key = f"{checkpoint.run_id}:{checkpoint.active_task_id}"
            return replace(
                checkpoint,
                external_state_guards=(checkpoint_guard,),
                task_effect_records=(TaskEffectRecord(
                    task_id=checkpoint.active_task_id,
                    tool_name="payment-provider",
                    operation_type="write",
                    idempotency_key=idempotency_key,
                    effect_state=SideEffectState.UNKNOWN,
                    external_reference="provider://resume-fixture/txn-18",
                ),),
                idempotency_keys=(idempotency_key,),
            )

        async def stop_after_activation(
            self,
            selected_workflow,
            context,
            *,
            checkpoint_request=None,
        ):
            nonlocal interrupted
            if not interrupted:
                interrupted = True
                raise RuntimeError("simulated process interruption")
            return await original_execute(
                self,
                selected_workflow,
                context,
                checkpoint_request=checkpoint_request,
            )

        async def emit_result(self, plan, task, context):
            stage_contexts.append(context)
            context.get_var("workspace").write_text(
                "output/resume.txt",
                "resume-result",
            )
            return ExecutionResult(
                success=True,
                outputs={"text": "resume-result"},
                metadata={"executor": "offline-fixture"},
            )

        monkeypatch.setattr(WorkflowExecutor, "execute", stop_after_activation)
        monkeypatch.setattr(
            RunResumeCoordinator,
            "_initial_activation_checkpoint",
            initial_checkpoint_with_guard,
        )
        monkeypatch.setattr(WorkflowExecutor, "_execute_plan", emit_result)
        monkeypatch.setattr(
            workflow_registry,
            "get",
            lambda workflow_id: (
                workflow if workflow_id == workflow.id else None
            ),
        )

        class CrashAfterActivationLauncher:
            async def start(self, *, session_context, run_context, request):
                view = run_context.durable_store_view
                view.transition_run_with_event(
                    run_status="RUNNING",
                    event_id=f"run-started:{run_context.run_id}:{request.request_id}",
                    event_type="run_started",
                    timestamp="2026-09-27T00:00:00Z",
                    payload={"request_id": request.request_id},
                    expected_status="CREATED",
                )
                proposed = new_single_workflow_index(
                    workflow,
                    run_id=run_context.run_id,
                    session_id=run_context.session_id,
                    conversation_id=run_context.session_id,
                    user_scope=run_context.session.learning_namespace,
                    target_summary=request.request_text,
                    input_bindings={"selected_source": "persisted-source"},
                )
                index = view.bootstrap_run_index(proposed)
                coordinator = RunResumeCoordinator(
                    runtime_store_view=view,
                    workflows={workflow.id: workflow},
                )
                await coordinator.execute_or_resume(
                    run_context.run_id,
                    lambda selected: workflow_execution_context(
                        selected,
                        index.workflow(selected.id),
                        run_context=run_context,
                    ),
                    attempt_id=request.request_id,
                )

            async def resume(self, *, run_context, request):
                raise AssertionError("the seed launcher must not resume")

        store_a = SqliteRuntimeStore.open(database)
        service_a = AgentService(
            runtime_store=store_a,
            launcher=CrashAfterActivationLauncher(),
            context_factory=ServiceContextFactory(
                store_a,
                workspace_root=tmp_path,
            ),
        )
        start = StartRunRequest(
            tenant_id="resume-tenant",
            user_id="resume-user",
            session_id="resume-session",
            request_id="resume-start",
            request_text="solve the original task",
        )
        try:
            handle = await service_a.start_run(start)
            for _ in range(100):
                index = store_a.get_run_index(
                    start.tenant_id,
                    handle.run_id,
                    session_id=start.session_id,
                )
                if index is not None and index.active_workflow_id:
                    break
                await asyncio.sleep(0.01)
            assert index is not None
            assert index.active_workflow_id == workflow.id
            assert index.workflow(workflow.id).target_summary == start.request_text
        finally:
            await service_a.close()
            if not store_a.closed:
                store_a.close()

        class NeverRuntime:
            def __init__(self, *_args, **_kwargs):
                raise AssertionError("resume must not construct UniversalAgent")

        store_b = SqliteRuntimeStore.open(database)
        service_b = AgentService(
            runtime_store=store_b,
            launcher=RuntimeExecutionLauncher(runtime_factory=NeverRuntime),
            context_factory=ServiceContextFactory(
                store_b,
                workspace_root=tmp_path,
            ),
        )
        def checked_now() -> str:
            return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        waiting_resume = ResumeRunRequest(
            tenant_id=start.tenant_id,
            user_id=start.user_id,
            session_id=start.session_id,
            run_id=handle.run_id,
            request_id="resume-wait-for-confirmation",
            request_text="核实上一次副作用状态",
            external_state_evidence=(ExternalStateEvidence(
                resource_id="object:resume-fixture",
                guard_type="OBJECT_VERSION",
                expected_value="v1",
                observed_value="v1",
                checked_at=checked_now(),
                status="VERIFIED",
            ),),
        )
        try:
            await service_b.resume_run(waiting_resume)
            waiting_task = service_b._runs[handle.run_id].task
            await asyncio.wait_for(waiting_task, timeout=5)
            waiting_events = service_b._event_repository().read_after(
                tenant_id=start.tenant_id,
                session_id=start.session_id,
                run_id=handle.run_id,
                after_sequence=0,
            )
            waiting_snapshot = await service_b.get_run(
                RunLookupRequest(
                    tenant_id=start.tenant_id,
                    user_id=start.user_id,
                    session_id=start.session_id,
                    run_id=handle.run_id,
                    request_id="waiting-snapshot",
                )
            )

            waiting_event_types = [event.event_type for event in waiting_events]
            assert EventType.RESUME_DECIDED in waiting_event_types
            assert waiting_event_types[-1] is EventType.RUN_WAITING_USER
            assert not waiting_events[-1].is_terminal
            assert waiting_snapshot.status is RunStatus.WAITING_USER
            assert waiting_snapshot.output is not None
            assert "确认" in waiting_snapshot.output.text
            waiting_checkpoint = store_b.latest_checkpoint(
                start.tenant_id,
                handle.run_id,
                session_id=start.session_id,
                workflow_id=workflow.id,
            )
            assert waiting_checkpoint is not None
            assert waiting_checkpoint.status.value == "WAITING_USER"
        finally:
            await service_b.close()
            if not store_b.closed:
                store_b.close()

        store_c = SqliteRuntimeStore.open(database)
        service_c = AgentService(
            runtime_store=store_c,
            launcher=RuntimeExecutionLauncher(runtime_factory=NeverRuntime),
            context_factory=ServiceContextFactory(
                store_c,
                workspace_root=tmp_path,
            ),
        )
        resume = ResumeRunRequest(
            tenant_id=start.tenant_id,
            user_id=start.user_id,
            session_id=start.session_id,
            run_id=handle.run_id,
            request_id="resume-confirmed-no-effect",
            request_text="已核实该副作用没有发生，继续原任务",
            external_state_evidence=(ExternalStateEvidence(
                resource_id="object:resume-fixture",
                guard_type="OBJECT_VERSION",
                expected_value="v1",
                observed_value="v1",
                checked_at=checked_now(),
                status="VERIFIED",
            ),),
            no_effect_confirmation=ServiceNoEffectConfirmation(
                task_id="emit",
                idempotency_key=f"{handle.run_id}:emit",
                external_reference="provider://resume-fixture/txn-18",
                checked_at=checked_now(),
            ),
        )
        try:
            await service_c.resume_run(resume)
            events = await asyncio.wait_for(
                _collect_events_for_scope(
                    service_c,
                    tenant_id=start.tenant_id,
                    user_id=start.user_id,
                    session_id=start.session_id,
                    run_id=handle.run_id,
                ),
                timeout=5,
            )
            snapshot = await service_c.get_run(
                RunLookupRequest(
                    tenant_id=start.tenant_id,
                    user_id=start.user_id,
                    session_id=start.session_id,
                    run_id=handle.run_id,
                    request_id="resumed-snapshot",
                )
            )

            event_types = [event.event_type for event in events]
            assert EventType.RUN_WAITING_USER in event_types
            assert event_types[-1] is EventType.RUN_COMPLETED
            waiting_index = event_types.index(EventType.RUN_WAITING_USER)
            assert EventType.RUN_RESUMED in event_types[waiting_index + 1:]
            assert snapshot.status is RunStatus.COMPLETED
            assert snapshot.output is not None
            assert snapshot.output.text == "工作流执行完成"
            assert len(stage_contexts) == 1
            assert stage_contexts[0].user_input == start.request_text
            assert (
                stage_contexts[0].get_artifact("selected_source").content
                == "persisted-source"
            )
            final_index = store_c.get_run_index(
                start.tenant_id,
                handle.run_id,
                session_id=start.session_id,
            )
            assert final_index is not None
            assert final_index.active_workflow_id == ""
            assert final_index.completed_workflow_ids == (workflow.id,)
            final_checkpoint = store_c.latest_checkpoint(
                start.tenant_id,
                handle.run_id,
                session_id=start.session_id,
                workflow_id=workflow.id,
            )
            assert final_checkpoint is not None
            assert any(
                item.kind == "external_state_guard"
                and "OBJECT_VERSION:object:resume-fixture;" in item.detail
                and "checked_at=" in item.detail
                for item in final_checkpoint.runtime_evidence
            )
            assert any(
                item.kind == "no_effect_confirmation"
                and item.status == "ATTESTED"
                and f"idempotency_key={handle.run_id}:emit" in item.detail
                for item in final_checkpoint.runtime_evidence
            )
        finally:
            await service_c.close()
            if not store_c.closed:
                store_c.close()

    asyncio.run(scenario())
