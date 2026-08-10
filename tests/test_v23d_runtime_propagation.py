from __future__ import annotations

import asyncio
import time

import pytest

from agent.interruption import (
    AtomicRegion,
    CancellationCoordinator,
    CancellationSafetyClass,
    CancelRunRequest,
    InterruptionPhase,
    RunInterruptionRequested,
    SafeCancellationBoundary,
    await_interruptibly,
    cancellation_scope,
)
from agent.executor.plan_executor import PlanExecutor
from agent.bootstrap import load_all
from agent.llm import LLMRouter
from agent.runtime import UniversalAgent
from agent.runtime_context import ApplicationContext
from agent.runtime_store import SqliteRuntimeStore
from agent.service import StartRunRequest
from agent.service.runtime_launcher import RuntimeExecutionLauncher
from agent.task import ExecutionPlan, ExecutionStep, Task, Verb


def _cancel(run_context, *, request_id: str = "cancel-1") -> CancelRunRequest:
    return CancelRunRequest(
        tenant_id=run_context.tenant_id,
        user_id=run_context.user_id,
        session_id=run_context.session_id,
        run_id=run_context.run_id,
        request_id=request_id,
        requested_by=run_context.user_id,
    )


def _scoped_run(tmp_path, *, run_id: str = "run-1"):
    store = SqliteRuntimeStore.open(tmp_path / f"{run_id}.sqlite")
    store.initialize_run(
        "tenant-a",
        "session-a",
        run_id,
        "start-1",
        run_status="CREATED",
    )
    application = ApplicationContext(
        runtime_store=store,
        workspace_root=tmp_path,
        runtime_writer_id="worker-a",
    )
    session = application.create_session(
        session_id="session-a",
        user_id="user-a",
        tenant_id="tenant-a",
    )
    run = session.create_run(
        run_id=run_id,
        request_id="start-1",
        workspace=tmp_path / run_id,
    )
    return store, application, session, run


def test_cancellation_view_is_read_only_and_respects_atomic_regions(tmp_path) -> None:
    store, application, _session, run = _scoped_run(tmp_path)
    try:
        saved = CancellationCoordinator(store).request_cancel(_cancel(run))
        assert run.cancellation_view is not None

        # An atomic region hides the intent until the next declared boundary.
        run.cancellation_view.raise_if_requested(
            SafeCancellationBoundary.BEFORE_TOOL,
            CancellationSafetyClass.BOUNDARY_ONLY,
            atomic_region=AtomicRegion.FILESYSTEM_ATOMIC_REPLACE,
        )
        with pytest.raises(RunInterruptionRequested) as caught:
            run.cancellation_view.raise_if_requested(
                SafeCancellationBoundary.BEFORE_TOOL,
                CancellationSafetyClass.BOUNDARY_ONLY,
            )

        assert caught.value.observation.record == saved
        # Observation is a read projection; only the Coordinator may advance.
        current = store.get_interruption("tenant-a", "run-1")
        assert current is not None
        assert current.intent.phase is InterruptionPhase.REQUESTED
    finally:
        application.close()


def test_runtime_stops_before_planner_without_finalizer_or_new_work(tmp_path) -> None:
    async def scenario() -> None:
        store, application, session, run = _scoped_run(tmp_path)
        try:
            CancellationCoordinator(store).request_cancel(_cancel(run))
            runtime = UniversalAgent(
                "user-a",
                tenant_id="tenant-a",
                session_context=session,
                run_context=run,
            )

            async def forbidden(*args, **kwargs):
                raise AssertionError("planner/finalizer must not start after cancellation")

            runtime.orchestrator.plan = forbidden  # type: ignore[method-assign]
            runtime.orchestrator.finalize = forbidden  # type: ignore[method-assign]
            answer = await runtime.run("create output/report.md")

            assert "取消" in answer
            assert runtime.last_run_evidence["interruption_requested"] is True
            assert runtime.last_run_evidence["terminal_status"] == "CANCELLING"
            assert runtime.last_run_evidence["interruption_boundary"] == "BEFORE_PLANNER"
            assert store.get_run_head("tenant-a", run.run_id).run_status == "CANCELLING"
        finally:
            application.close()

    asyncio.run(scenario())


class _RequestingRuntime:
    def __init__(self, *args, run_context, **kwargs) -> None:
        self.run_context = run_context
        self.last_run_evidence: dict[str, object] = {}
        self.closed = False

    async def run(self, _request_text: str) -> str:
        CancellationCoordinator(self.run_context.durable_store_view.store).request_cancel(
            _cancel(self.run_context)
        )
        self.last_run_evidence = {
            "interruption_requested": True,
            "interruption_request_id": "cancel-1",
            "terminal_status": "CANCELLING",
        }
        return "stopped"

    def close(self) -> None:
        self.closed = True


def test_launcher_converges_observed_intent_to_single_cancelled_terminal(tmp_path) -> None:
    async def scenario() -> None:
        store, application, session, run = _scoped_run(tmp_path)
        launcher = RuntimeExecutionLauncher(runtime_factory=_RequestingRuntime)
        request = StartRunRequest(
            tenant_id="tenant-a",
            user_id="user-a",
            session_id="session-a",
            run_id=run.run_id,
            request_id="start-1",
            request_text="create output/report.md",
        )
        try:
            await launcher.start(
                session_context=session,
                run_context=run,
                request=request,
            )

            head = store.get_run_head("tenant-a", run.run_id)
            assert head is not None and head.run_status == "CANCELLED"
            events = store.read_events(
                "tenant-a", run.run_id, session_id="session-a"
            )
            assert [event.event_type for event in events].count("run_cancelled") == 1
            assert all(event.event_type != "run_completed" for event in events)
            intent = store.get_interruption("tenant-a", run.run_id)
            assert intent is not None
            assert intent.intent.phase is InterruptionPhase.FINALIZED
        finally:
            application.close()

    asyncio.run(scenario())


def test_interruptible_provider_wait_aborts_without_fallback(tmp_path) -> None:
    class _Provider:
        def __init__(self) -> None:
            self.calls = 0
            self.started = asyncio.Event()
            self.stopped = asyncio.Event()

        async def ainvoke(self, _messages, **_kwargs):
            self.calls += 1
            self.started.set()
            try:
                await asyncio.Event().wait()
            finally:
                self.stopped.set()

    async def scenario() -> None:
        store, application, _session, run = _scoped_run(
            tmp_path, run_id="provider-run"
        )
        primary = _Provider()
        fallback = _Provider()
        router = LLMRouter()
        router._deepseek = primary  # type: ignore[assignment]
        router._ollama = fallback  # type: ignore[assignment]
        try:
            assert run.cancellation_view is not None
            started_at = time.perf_counter()
            with cancellation_scope(run.cancellation_view):
                operation = asyncio.create_task(router.ainvoke([]))
                await asyncio.wait_for(primary.started.wait(), timeout=1)
                CancellationCoordinator(store).request_cancel(_cancel(run))
                with pytest.raises(RunInterruptionRequested):
                    await asyncio.wait_for(operation, timeout=1)

            assert time.perf_counter() - started_at < 1
            assert primary.calls == 1
            assert fallback.calls == 0
            assert primary.stopped.is_set()
        finally:
            application.close()

    asyncio.run(scenario())


def test_boundary_only_file_effect_finishes_then_blocks_next_effect(
    tmp_path, monkeypatch
) -> None:
    async def scenario() -> None:
        load_all()
        store, application, _session, run = _scoped_run(
            tmp_path, run_id="file-run"
        )
        original_write = run.workspace.write_text
        calls: list[str] = []

        def write_then_cancel(path: str, content: str, *, mode: str = "overwrite"):
            calls.append(path)
            result = original_write(path, content, mode=mode)
            if len(calls) == 1:
                CancellationCoordinator(store).request_cancel(_cancel(run))
            return result

        monkeypatch.setattr(run.workspace, "write_text", write_then_cancel)
        plan = ExecutionPlan(
            task=Task(
                id="two-writes",
                verb=Verb.WRITE,
                target="output/first.txt",
                target_type="file",
            ),
            steps=[
                ExecutionStep(
                    tool="filesystem.write",
                    args={"path": "output/first.txt", "content": "first"},
                ),
                ExecutionStep(
                    tool="filesystem.write",
                    args={"path": "output/second.txt", "content": "second"},
                ),
            ],
        )
        try:
            assert run.cancellation_view is not None
            with pytest.raises(RunInterruptionRequested) as caught:
                await PlanExecutor().execute(
                    plan,
                    workspace=run.workspace,
                    cancellation_view=run.cancellation_view,
                )

            assert calls == ["output/first.txt"]
            assert (tmp_path / "file-run/output/first.txt").read_text() == "first"
            assert not (tmp_path / "file-run/output/second.txt").exists()
            assert caught.value.execution_evidence["completed_tool"] == "filesystem.write"
            assert caught.value.execution_evidence["files_written"] == [
                "output/first.txt"
            ]
        finally:
            application.close()

    asyncio.run(scenario())


class _TimedRuntime:
    def __init__(self, *args, run_context, **kwargs) -> None:
        self.run_context = run_context
        self.last_run_evidence: dict[str, object] = {}
        self.provider_started = asyncio.Event()
        self.provider_stopped = asyncio.Event()

    async def _provider_wait(self) -> None:
        self.provider_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.provider_stopped.set()

    async def run(self, _request_text: str) -> str:
        with cancellation_scope(self.run_context.cancellation_view):
            try:
                await await_interruptibly(self._provider_wait())
            except RunInterruptionRequested as interruption:
                intent = interruption.observation.record.intent
                self.last_run_evidence = {
                    "interruption_requested": True,
                    "interruption_request_id": intent.request_id,
                    "interruption_reason": intent.reason.value,
                    "terminal_status": "CANCELLING",
                }
                return "timed out at safe boundary"
        raise AssertionError("watchdog did not interrupt provider wait")

    def close(self) -> None:
        pass


def test_run_watchdog_persists_timeout_then_converges_at_provider_boundary(
    tmp_path,
) -> None:
    async def scenario() -> None:
        store, application, session, run = _scoped_run(
            tmp_path, run_id="timeout-run"
        )
        instances: list[_TimedRuntime] = []

        def factory(*args, **kwargs):
            runtime = _TimedRuntime(*args, **kwargs)
            instances.append(runtime)
            return runtime

        launcher = RuntimeExecutionLauncher(runtime_factory=factory)
        request = StartRunRequest(
            tenant_id="tenant-a",
            user_id="user-a",
            session_id="session-a",
            run_id=run.run_id,
            request_id="start-1",
            request_text="wait for provider",
            metadata={"run_timeout_seconds": 0.05},
        )
        try:
            started_at = time.perf_counter()
            await asyncio.wait_for(
                launcher.start(
                    session_context=session,
                    run_context=run,
                    request=request,
                ),
                timeout=2,
            )

            assert time.perf_counter() - started_at < 1
            assert len(instances) == 1
            assert instances[0].provider_started.is_set()
            assert instances[0].provider_stopped.is_set()
            intent = store.get_interruption("tenant-a", run.run_id)
            assert intent is not None
            assert intent.intent.reason.value == "RUN_TIMEOUT"
            assert intent.intent.phase is InterruptionPhase.FINALIZED
            head = store.get_run_head("tenant-a", run.run_id)
            assert head is not None and head.run_status == "TIMED_OUT"
            events = store.read_events(
                "tenant-a", run.run_id, session_id="session-a"
            )
            assert [event.event_type for event in events].count("run_timed_out") == 1
            assert all(event.event_type != "run_completed" for event in events)
        finally:
            application.close()

    asyncio.run(scenario())


class _ProviderTimeoutRuntime:
    def __init__(self, *args, **kwargs) -> None:
        self.last_run_evidence = {
            "terminal_status": "FAILED_TERMINAL",
            "terminal_outputs_verified": False,
            "runtime_pending": False,
            "task_failures": [{"id": "provider"}],
            "failure_code": "PROVIDER_TIMEOUT",
            "failed_component": "planner_llm",
        }

    async def run(self, _request_text: str) -> str:
        return "provider timed out"

    def close(self) -> None:
        pass


def test_provider_timeout_does_not_create_run_timeout_intent(tmp_path) -> None:
    async def scenario() -> None:
        store, application, session, run = _scoped_run(
            tmp_path, run_id="provider-timeout-run"
        )
        launcher = RuntimeExecutionLauncher(runtime_factory=_ProviderTimeoutRuntime)
        request = StartRunRequest(
            tenant_id="tenant-a",
            user_id="user-a",
            session_id="session-a",
            run_id=run.run_id,
            request_id="start-1",
            request_text="provider timeout probe",
        )
        try:
            await launcher.start(
                session_context=session,
                run_context=run,
                request=request,
            )
            assert store.get_interruption("tenant-a", run.run_id) is None
            head = store.get_run_head("tenant-a", run.run_id)
            assert head is not None and head.run_status == "FAILED_TERMINAL"
            assert all(
                event.event_type != "run_timed_out"
                for event in store.read_events(
                    "tenant-a", run.run_id, session_id="session-a"
                )
            )
        finally:
            application.close()

    asyncio.run(scenario())
