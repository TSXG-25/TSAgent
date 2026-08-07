"""C-4 durable Runtime state-event wiring tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.run_resume import (
    RunResumeCoordinator,
    RunResumeIndex,
    RunWorkflowStatus,
    WorkflowDependency,
    WorkflowSummary,
)
from agent.runtime_store import DurableStoreError, SqliteRuntimeStore, StoreErrorCode
from agent.runtime_store.view import DurableRuntimeStoreView
from agent.workflow import ExecutionSpec, ExecutorType, Stage, Workflow


def _workflow() -> Workflow:
    return Workflow(
        id="workflow-a",
        version="1.0.0",
        description="C-4 event workflow",
        stages=[
            Stage(
                id="stage-a",
                execution=ExecutionSpec(executor=ExecutorType.TOOL),
                description="deterministic stage",
            )
        ],
    )


def _pending_index(store: SqliteRuntimeStore) -> RunResumeIndex:
    summary = WorkflowSummary(
        workflow_id="workflow-a",
        workflow_version="1.0.0",
        status=RunWorkflowStatus.PENDING,
    )
    return RunResumeIndex(
        run_id="run-events",
        workflow_sequence=("workflow-a",),
        workflows=(summary,),
        completed_workflow_ids=(),
        active_workflow_id="",
        active_checkpoint_id="",
        pending_workflow_ids=("workflow-a",),
        workflow_dependencies=(WorkflowDependency("workflow-a"),),
        store_generation=store.store_generation,
        session_id="session-events",
        conversation_id="conversation-events",
        user_scope="user-events",
        created_at="2026-08-07T00:00:00Z",
        updated_at="2026-08-07T00:00:00Z",
    )


def test_workflow_activation_publishes_event_in_same_durable_path(
    tmp_path: Path,
) -> None:
    store = SqliteRuntimeStore.open(tmp_path / "activation-events.sqlite")
    view = DurableRuntimeStoreView(
        store,
        tenant_id="tenant-events",
        session_id="session-events",
        run_id="run-events",
        request_id="request-events",
        writer_id="writer-events",
    )
    try:
        index = _pending_index(store).evolve(parent_digest="")
        store.append_revision(
            "tenant-events",
            "session-events",
            "run-events",
            request_id="request-events",
            payload=index.to_dict(),
            writer_id=view.writer_id,
            fence_token=view.fence_epoch,
            expected_revision=0,
            expected_parent_digest="",
            run_status="RUNNING",
        )
        workflow = _workflow()
        coordinator = RunResumeCoordinator(
            runtime_store_view=view,
            workflows={workflow.id: workflow},
        )
        checkpoint = coordinator._initial_activation_checkpoint(
            index,
            workflow,
            "attempt-events",
        )

        activated = view.activate_workflow(
            workflow.id,
            expected_revision=1,
            attempt_id="attempt-events",
            initial_checkpoint=checkpoint,
        )

        assert activated.active_workflow_id == workflow.id
        events = store.read_events(
            "tenant-events",
            "run-events",
            session_id="session-events",
        )
        assert [event.event_type for event in events] == ["workflow_activated"]
        assert events[0].workflow_id == workflow.id
        assert events[0].run_revision == activated.revision
    finally:
        view.close()
        store.close()


def test_terminal_run_cannot_reopen_through_state_event_transition(
    tmp_path: Path,
) -> None:
    store = SqliteRuntimeStore.open(tmp_path / "terminal-events.sqlite")
    view = DurableRuntimeStoreView(
        store,
        tenant_id="tenant-terminal",
        session_id="session-terminal",
        run_id="run-terminal",
        request_id="request-terminal",
        writer_id="writer-terminal",
    )
    try:
        view.transition_run_with_event(
            run_status="FAILED_TERMINAL",
            event_id="run-failed:run-terminal",
            event_type="run_failed",
            timestamp="2026-08-07T00:00:00Z",
            payload={"reason": "test"},
        )
        with pytest.raises(DurableStoreError) as error:
            view.transition_run_with_event(
                run_status="RUNNING",
                event_id="run-reopened:run-terminal",
                event_type="run_resumed",
                timestamp="2026-08-07T00:00:01Z",
                payload={},
            )
        assert error.value.code is StoreErrorCode.REVISION_CONFLICT
        head = store.get_run_head(
            "tenant-terminal",
            "run-terminal",
            session_id="session-terminal",
        )
        assert head is not None
        assert head.run_status == "FAILED_TERMINAL"
    finally:
        view.close()
        store.close()
