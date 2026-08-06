"""v2.3B-4 production-path migration tests.

These tests use a deterministic provider, but exercise the same
ApplicationContext -> RunContext -> Coordinator -> WorkflowExecutor path that
production uses when a SQLite Runtime Store is configured.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agent.executor.contract import executor_factory
from agent.run_resume import (
    RunResumeIndex,
    RunWorkflowStatus,
    WorkflowDependency,
    WorkflowSummary,
    RunResumeCoordinator,
)
from agent.runtime_context import ApplicationContext
from agent.runtime_store import (
    DurableStoreError,
    SqliteRuntimeStore,
    StoreErrorCode,
)
from agent.workflow import (
    ExecutionContext,
    ExecutionResult,
    ExecutionSpec,
    ExecutorType,
    OutputArtifact,
    Stage,
    ToolArgument,
    Workflow,
)


class FileWritingProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, task, context):
        self.calls += 1
        path = Path(str(task.inputs.get("path", "")))
        content = str(task.inputs.get("content", "durable-result"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return ExecutionResult(
            success=True,
            outputs={"text": content},
            metadata={"executor": "deterministic-file-provider"},
        )


def _workflow(path: Path) -> Workflow:
    return Workflow(
        id="wf-durable",
        version="1.0.0",
        description="write durable result",
        stages=[
            Stage(
                id="stage-write",
                execution=ExecutionSpec(executor=ExecutorType.TOOL),
                description="write durable result",
                arguments=[
                    ToolArgument(param="path", constant=str(path)),
                    ToolArgument(param="content", constant="durable-result"),
                ],
                outputs=[OutputArtifact(type="result")],
            )
        ],
    )


def _active_index(run_id: str, session_id: str) -> RunResumeIndex:
    summary = WorkflowSummary(
        workflow_id="wf-durable",
        workflow_version="1.0.0",
        status=RunWorkflowStatus.RUNNING,
        activation_attempt_id="attempt-1",
    )
    return RunResumeIndex(
        run_id=run_id,
        workflow_sequence=("wf-durable",),
        workflows=(summary,),
        completed_workflow_ids=(),
        active_workflow_id="wf-durable",
        active_checkpoint_id="",
        pending_workflow_ids=(),
        workflow_dependencies=(WorkflowDependency("wf-durable"),),
        session_id=session_id,
        conversation_id="conversation-1",
        user_scope="user-1",
    )


def _pending_index(run_id: str, session_id: str) -> RunResumeIndex:
    summary = WorkflowSummary(
        workflow_id="wf-durable",
        workflow_version="1.0.0",
        status=RunWorkflowStatus.PENDING,
    )
    return RunResumeIndex(
        run_id=run_id,
        workflow_sequence=("wf-durable",),
        workflows=(summary,),
        completed_workflow_ids=(),
        active_workflow_id="",
        active_checkpoint_id="",
        pending_workflow_ids=("wf-durable",),
        workflow_dependencies=(WorkflowDependency("wf-durable"),),
        session_id=session_id,
        conversation_id="conversation-1",
        user_scope="user-1",
    )


def test_configured_runtime_uses_scoped_sqlite_and_finalization_bundle(tmp_path):
    database = tmp_path / "runtime.sqlite"
    output = tmp_path / "output" / "result.txt"
    provider = FileWritingProvider()
    original = dict(executor_factory._registry)
    executor_factory._registry.clear()
    executor_factory.register("tool", lambda: provider)
    executor_factory.register("llm", lambda: provider)
    try:
        store = SqliteRuntimeStore.open(database)
        app = ApplicationContext(
            runtime_store=store,
            runtime_writer_id="writer-a",
        )
        session = app.create_session(
            "session-1",
            user_id="user-1",
            tenant_id="tenant-1",
        )
        run = session.create_run("run-1", request_id="request-1")
        assert run.durable_store_view is not None
        run.durable_store_view.bootstrap_run_index(
            _active_index("run-1", "session-1")
        )

        coordinator = RunResumeCoordinator(
            runtime_store_view=run.durable_store_view,
            workflows={"wf-durable": _workflow(output)},
        )
        execution = asyncio.run(
            coordinator.resume_active(
                "run-1",
                ExecutionContext(workflow_id="wf-durable"),
            )
        )

        assert execution.execution_result is not None
        assert execution.execution_result.success, execution.execution_result.error
        assert provider.calls == 1
        assert output.read_text(encoding="utf-8") == "durable-result"
        assert execution.index.completed_workflow_ids == ("wf-durable",)
        assert execution.index.active_workflow_id == ""
        assert run.durable_store_view.get_run_index() == execution.index
        assert len(run.durable_store_view.checkpoint_history(workflow_id="wf-durable")) >= 4

        with pytest.raises(DurableStoreError) as error:
            run.checkpoint_store.save(execution.index)  # type: ignore[arg-type]
        assert error.value.code is StoreErrorCode.INVALID_ARGUMENT
        app.close()

        reopened = SqliteRuntimeStore.open(database)
        try:
            assert reopened.get_run_index("tenant-1", "run-1", session_id="session-1") == execution.index
            row = reopened.connection.execute(
                "SELECT effect_state FROM idempotency_ledger WHERE tenant_id = ? AND run_id = ?",
                ("tenant-1", "run-1"),
            ).fetchone()
            assert row is not None and row["effect_state"] == "COMMITTED"
        finally:
            reopened.close()
    finally:
        executor_factory._registry.clear()
        executor_factory._registry.update(original)


def test_durable_activation_publishes_initial_checkpoint_before_executor(tmp_path):
    database = tmp_path / "activation.sqlite"
    output = tmp_path / "output" / "activation.txt"
    provider = FileWritingProvider()
    original = dict(executor_factory._registry)
    executor_factory._registry.clear()
    executor_factory.register("tool", lambda: provider)
    executor_factory.register("llm", lambda: provider)
    try:
        store = SqliteRuntimeStore.open(database)
        app = ApplicationContext(runtime_store=store, runtime_writer_id="writer-a")
        session = app.create_session("session-1", user_id="user-1", tenant_id="tenant-1")
        run = session.create_run("run-1", request_id="request-1")
        assert run.durable_store_view is not None
        run.durable_store_view.bootstrap_run_index(_pending_index("run-1", "session-1"))
        coordinator = RunResumeCoordinator(
            runtime_store_view=run.durable_store_view,
            workflows={"wf-durable": _workflow(output)},
        )
        execution = asyncio.run(
            coordinator.execute_or_resume(
                "run-1",
                lambda workflow: ExecutionContext(workflow_id=workflow.id),
                attempt_id="attempt-1",
            )
        )
        assert execution.execution_result is not None
        assert execution.execution_result.success, execution.execution_result.error
        assert provider.calls == 1
        history = run.durable_store_view.checkpoint_history(workflow_id="wf-durable")
        assert history[0].sequence_number == 0
        assert history[0].status.value == "RUNNING"
        assert history[-1].status.value == "COMPLETED"
        app.close()
    finally:
        executor_factory._registry.clear()
        executor_factory._registry.update(original)
