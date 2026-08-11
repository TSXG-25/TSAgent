from __future__ import annotations

from pathlib import Path

from agent.interruption import (
    CancelRunRequest,
    CancellationCoordinator,
    InterruptionPhase,
    interruption_policy,
)
from agent.run_resume import (
    RunArtifactFact,
    RunResumeIndex,
    RunWorkflowStatus,
    WorkflowDependency,
    WorkflowSummary,
)
from agent.runtime_store import SqliteRuntimeStore
from agent.runtime_store.view import DurableRuntimeStoreView


def _multi_goal_index(store: SqliteRuntimeStore) -> RunResumeIndex:
    workflows = (
        WorkflowSummary(
            workflow_id="workflow-a",
            workflow_version="1.0.0",
            status=RunWorkflowStatus.COMPLETED,
            checkpoint_id="checkpoint-a",
            verifier_status="VERIFIED",
        ),
        WorkflowSummary(
            workflow_id="workflow-b",
            workflow_version="1.0.0",
            status=RunWorkflowStatus.RUNNING,
            checkpoint_id="checkpoint-b",
            activation_attempt_id="attempt-b",
            depends_on=("workflow-a",),
            verifier_status="PENDING",
        ),
        WorkflowSummary(
            workflow_id="workflow-c",
            workflow_version="1.0.0",
            status=RunWorkflowStatus.PENDING,
            depends_on=("workflow-b",),
        ),
    )
    return RunResumeIndex(
        run_id="run-d404",
        workflow_sequence=("workflow-a", "workflow-b", "workflow-c"),
        workflows=workflows,
        completed_workflow_ids=("workflow-a",),
        active_workflow_id="workflow-b",
        active_checkpoint_id="checkpoint-b",
        pending_workflow_ids=("workflow-c",),
        workflow_dependencies=(
            WorkflowDependency("workflow-a"),
            WorkflowDependency("workflow-b", ("workflow-a",)),
            WorkflowDependency("workflow-c", ("workflow-b",)),
        ),
        artifacts=(
            RunArtifactFact(
                artifact_id="artifact-a",
                producer_workflow_id="workflow-a",
                digest="digest-a",
                artifact_type="text/plain",
                reference="opaque://artifact-a",
            ),
        ),
        store_generation=store.store_generation,
        session_id="session-d404",
        conversation_id="conversation-d404",
        user_scope="user-d404",
        created_at="2026-08-11T00:00:00Z",
        updated_at="2026-08-11T00:00:00Z",
    )


def test_d404_cancel_preserves_completed_workflow_and_pending_tail(
    tmp_path: Path,
) -> None:
    store = SqliteRuntimeStore.open(tmp_path / "d404.sqlite")
    view = DurableRuntimeStoreView(
        store,
        tenant_id="tenant-d404",
        session_id="session-d404",
        run_id="run-d404",
        request_id="request-d404",
        writer_id="writer-d404",
    )
    try:
        index = _multi_goal_index(store).evolve(parent_digest="")
        store.append_revision(
            "tenant-d404",
            "session-d404",
            "run-d404",
            request_id="request-d404",
            payload=index.to_dict(),
            writer_id=view.writer_id,
            fence_token=view.fence_epoch,
            expected_revision=0,
            expected_parent_digest="",
            run_status="RUNNING",
        )

        cancellation = CancellationCoordinator(store)
        cancellation.request_cancel(
            CancelRunRequest(
                tenant_id="tenant-d404",
                user_id="user-d404",
                session_id="session-d404",
                run_id="run-d404",
                request_id="cancel-d404",
                requested_by="user-d404",
            )
        )
        current_fence = store.get_current_fence(
            "tenant-d404", "run-d404", session_id="session-d404"
        )
        assert current_fence is not None
        finalized = cancellation.mark_safe_to_interrupt(
            tenant_id="tenant-d404",
            session_id="session-d404",
            run_id="run-d404",
            request_id="cancel-d404",
            writer_id=current_fence.writer_id,
            fence_token=current_fence.fence_token,
        )

        assert finalized.intent.phase is InterruptionPhase.FINALIZED
        assert interruption_policy(finalized.intent.reason).resulting_status.value == "CANCELLED"
        preserved = store.get_run_index(
            "tenant-d404", "run-d404", session_id="session-d404"
        )
        assert preserved is not None
        assert preserved.completed_workflow_ids == ("workflow-a",)
        assert preserved.active_workflow_id == "workflow-b"
        assert preserved.pending_workflow_ids == ("workflow-c",)
        assert preserved.artifacts[0].artifact_id == "artifact-a"

        events = store.read_events(
            "tenant-d404", "run-d404", session_id="session-d404"
        )
        assert [event.event_type for event in events] == [
            "run_cancelling",
            "run_cancelled",
        ]
        assert all(event.workflow_id != "workflow-c" for event in events)
        head = store.get_run_head(
            "tenant-d404", "run-d404", session_id="session-d404"
        )
        assert head is not None and head.run_status == "CANCELLED"
    finally:
        view.close()
        store.close()
