from __future__ import annotations

import asyncio

import pytest

from agent.interruption import (
    AtomicRegion,
    CancellationCoordinator,
    CancellationSafetyClass,
    CancelRunRequest,
    InterruptionPhase,
    RunInterruptionRequested,
    SafeCancellationBoundary,
)
from agent.runtime import UniversalAgent
from agent.runtime_context import ApplicationContext
from agent.runtime_store import SqliteRuntimeStore
from agent.service import StartRunRequest
from agent.service.runtime_launcher import RuntimeExecutionLauncher


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
