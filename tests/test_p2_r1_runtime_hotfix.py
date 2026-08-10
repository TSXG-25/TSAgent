from __future__ import annotations

from pathlib import Path

import pytest

from agent.run_resume import (
    RunResumeIndex,
    RunWorkflowStatus,
    WorkflowDependency,
    WorkflowSummary,
)
from agent.runtime_store import (
    DurableRuntimeStoreView,
    DurableStoreError,
    SqliteRuntimeStore,
    StoreErrorCode,
)
from agent.executor.executors.workflow import WorkflowExecutor
from agent.services.workspace_service import WorkspaceService
from agent.workflow import (
    ExecutionContext,
    ExecutionSpec,
    ExecutorType,
    Stage,
    ToolArgument,
)


def _pending_index() -> RunResumeIndex:
    return RunResumeIndex(
        run_id="run-1",
        workflow_sequence=("wf-a",),
        workflows=(
            WorkflowSummary(
                workflow_id="wf-a",
                workflow_version="1.0.0",
                status=RunWorkflowStatus.PENDING,
            ),
        ),
        completed_workflow_ids=(),
        active_workflow_id="",
        active_checkpoint_id="",
        pending_workflow_ids=("wf-a",),
        workflow_dependencies=(WorkflowDependency("wf-a"),),
        session_id="session-1",
        conversation_id="session-1",
        user_scope="user-1",
    )


def _reserve_service_start(store: SqliteRuntimeStore) -> None:
    store.reserve_service_start(
        "tenant-1",
        "session-1",
        requested_run_id="run-1",
        request_id="request-1",
        request_digest="digest-1",
        writer_id="writer-a",
        external_reference="service-start:run-1",
    )


def test_run_index_bootstraps_behind_durable_service_start(tmp_path) -> None:
    store = SqliteRuntimeStore.open(tmp_path / "runtime.sqlite")
    try:
        _reserve_service_start(store)
        start_head = store.get_run_head("tenant-1", "run-1", session_id="session-1")
        assert start_head is not None and start_head.current_revision == 1
        view = DurableRuntimeStoreView(
            store,
            tenant_id="tenant-1",
            session_id="session-1",
            run_id="run-1",
            request_id="request-1",
            writer_id="writer-a",
        )

        seeded = view.bootstrap_run_index(_pending_index())

        assert seeded.revision == 2
        assert seeded.parent_digest == start_head.current_digest
        assert store.get_run_index(
            "tenant-1", "run-1", session_id="session-1"
        ) == seeded
        assert view.bootstrap_run_index(_pending_index()) == seeded
        view.close()
    finally:
        store.close()


def test_bootstrap_rejects_non_service_revision(tmp_path) -> None:
    store = SqliteRuntimeStore.open(tmp_path / "runtime.sqlite")
    try:
        store.initialize_run("tenant-1", "session-1", "run-1", "request-1")
        first = store.acquire_fence(
            "tenant-1", "session-1", "run-1", "writer-a"
        )
        store.append_revision(
            "tenant-1",
            "session-1",
            "run-1",
            request_id="other-operation",
            payload={"operation": "not-service-start"},
            writer_id="writer-a",
            fence_token=first.fence_token,
            expected_revision=0,
            expected_parent_digest="",
        )
        view = DurableRuntimeStoreView(
            store,
            tenant_id="tenant-1",
            session_id="session-1",
            run_id="run-1",
            request_id="request-1",
            writer_id="writer-a",
        )

        with pytest.raises(DurableStoreError) as raised:
            view.bootstrap_run_index(_pending_index())

        assert raised.value.code is StoreErrorCode.RUN_INDEX_CONFLICT
        view.close()
    finally:
        store.close()


def test_explicit_resume_takeover_fences_old_writer(tmp_path) -> None:
    store = SqliteRuntimeStore.open(tmp_path / "runtime.sqlite")
    try:
        _reserve_service_start(store)
        old_head = store.get_run_head("tenant-1", "run-1", session_id="session-1")
        assert old_head is not None and old_head.current_fence_token == 1

        resumed = DurableRuntimeStoreView(
            store,
            tenant_id="tenant-1",
            session_id="session-1",
            run_id="run-1",
            request_id="resume-1",
            writer_id="writer-b",
            takeover_fence=True,
        )

        assert resumed.fence_epoch == 2
        with pytest.raises(DurableStoreError) as raised:
            store.append_revision(
                "tenant-1",
                "session-1",
                "run-1",
                request_id="stale-write",
                payload={"stale": True},
                writer_id="writer-a",
                fence_token=1,
                expected_revision=old_head.current_revision,
                expected_parent_digest=old_head.current_digest,
            )
        assert raised.value.code is StoreErrorCode.STALE_WRITER
        resumed.close()
    finally:
        store.close()


def test_committed_file_recovery_uses_scoped_run_workspace(tmp_path) -> None:
    workspace_root = tmp_path / "run-workspace"
    workspace = WorkspaceService.scoped(workspace_root, build_index=False)
    try:
        workspace.write_text("output/effect.txt", "committed-once\n")
        stage = Stage(
            id="write-effect",
            description="write one effect",
            execution=ExecutionSpec(executor=ExecutorType.TOOL),
            arguments=[
                ToolArgument(param="path", constant="output/effect.txt"),
                ToolArgument(param="content", constant="committed-once\n"),
            ],
        )
        task = stage.to_task(goal=stage.description)
        context = ExecutionContext(workflow_id="wf-effect")
        context.set_var("workspace", workspace)

        state, reference = WorkflowExecutor._recover_committed_file_effect(
            task,
            {
                "path": "output/effect.txt",
                "content": "committed-once\n",
            },
            context=context,
            resume_mode=True,
        )

        assert state == "COMMITTED"
        assert Path(reference) == workspace_root / "output" / "effect.txt"
    finally:
        workspace.close()
