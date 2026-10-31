"""Run-bound construction for the canonical Workflow resume path."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import uuid4

from agent.task import Task, Verb
from agent.workflow import Artifact, ExecutionContext, Workflow

from .contracts import (
    RunResumeIndex,
    RunWorkflowStatus,
    WorkflowDependency,
    WorkflowSummary,
)


def new_single_workflow_index(
    workflow: Workflow,
    *,
    run_id: str,
    session_id: str,
    conversation_id: str,
    user_scope: str,
    target_summary: str,
    input_bindings: Mapping[str, Any],
) -> RunResumeIndex:
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    summary = WorkflowSummary(
        workflow_id=workflow.id,
        workflow_version=workflow.version,
        status=RunWorkflowStatus.PENDING,
        target_summary=target_summary,
        input_bindings=input_bindings,
    )
    return RunResumeIndex(
        run_id=run_id,
        workflow_sequence=(workflow.id,),
        workflows=(summary,),
        completed_workflow_ids=(),
        active_workflow_id="",
        active_checkpoint_id="",
        pending_workflow_ids=(workflow.id,),
        workflow_dependencies=(WorkflowDependency(workflow.id),),
        created_at=now,
        updated_at=now,
        session_id=session_id,
        conversation_id=conversation_id,
        user_scope=user_scope,
    )


def workflow_execution_context(
    workflow: Workflow,
    summary: WorkflowSummary,
    *,
    run_context: Any | None,
) -> ExecutionContext:
    context = ExecutionContext(
        workflow_id=workflow.id,
        user_input=summary.target_summary,
        task=Task(
            id="root",
            verb=Verb.READ,
            target="",
            goal=summary.target_summary,
        ),
    )
    for binding_name, binding_value in summary.input_bindings.items():
        context.set_artifact(Artifact(
            id=f"workflow-binding-{binding_name}",
            type=binding_name,
            content=binding_value,
            summary=str(binding_value),
        ))

    if run_context is not None:
        for name, value in (
            ("workspace", run_context.workspace),
            ("cancellation_view", run_context.cancellation_view),
            ("repository_indexer", run_context.repository_indexer),
            ("artifact_store", run_context.artifacts),
            ("event_bus", run_context.event_bus),
        ):
            if value is not None:
                context.set_var(name, value)
    return context


def ephemeral_run_id() -> str:
    return f"workflow-{uuid4().hex}"


__all__ = [
    "ephemeral_run_id",
    "new_single_workflow_index",
    "workflow_execution_context",
]
