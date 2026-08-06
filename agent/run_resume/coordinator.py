"""Minimal v2.2C Run-level resume entry.

The coordinator selects one Workflow from a Run and delegates Stage/Task
execution to the existing v2.2B ``WorkflowExecutor``.  It does not plan,
schedule concurrently, or implement a second Workflow executor.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable, Mapping

from agent.checkpoint import (
    CheckpointStatus,
    CheckpointStore,
    RunCheckpoint,
    ResumeContext,
    WorkflowCheckpointRequest,
)
from agent.checkpoint.recorder import fact_digest
from agent.executor.executors.workflow import WorkflowExecutor
from agent.workflow import (
    ExecutionContext,
    ExecutionResult,
    Workflow,
    hydrate_run_artifacts,
)

from .codec import run_index_digest
from .contracts import (
    RunArtifactFact,
    RunResumeDisposition,
    RunResumeIndex,
    RunResumeReasonCode,
    RunResumeRequest,
    RunWorkflowStatus,
)
from .resolver import RunResumeDecision, RunResumeResolver
from .store import RunResumeActivationError, RunResumeStore
from agent.runtime_store import (
    ArtifactCommitFact,
    CheckpointStagingBuffer,
    FinalizationBundle,
)
from agent.runtime_store.view import DurableRuntimeStoreView


@dataclass(frozen=True)
class RunResumeExecution:
    decision: RunResumeDecision
    execution_result: ExecutionResult | None
    index: RunResumeIndex


class RunResumeCoordinator:
    """Select and resume the active Workflow of one Run."""

    def __init__(
        self,
        *,
        run_store: RunResumeStore | None = None,
        checkpoint_store: CheckpointStore | None = None,
        workflows: Mapping[str, Workflow],
        workflow_executor: WorkflowExecutor | None = None,
        clock: Any | None = None,
        runtime_store_view: DurableRuntimeStoreView | None = None,
    ) -> None:
        if runtime_store_view is not None and (
            run_store is not None or checkpoint_store is not None
        ):
            raise ValueError(
                "durable Runtime Store 模式不能同时注入 legacy Run/Checkpoint Store"
            )
        if runtime_store_view is None and (run_store is None or checkpoint_store is None):
            raise ValueError(
                "必须提供 legacy stores，或提供 runtime_store_view"
            )
        self._durable_store = runtime_store_view
        self._run_store: RunResumeStore
        self._checkpoint_store: CheckpointStore
        self._run_store = (
            runtime_store_view.run_resume_store
            if runtime_store_view is not None
            else run_store  # type: ignore[assignment]
        )
        self._checkpoint_store = (
            runtime_store_view.checkpoint_store
            if runtime_store_view is not None
            else checkpoint_store  # type: ignore[assignment]
        )
        self._workflows = dict(workflows)
        self._workflow_executor = workflow_executor or WorkflowExecutor()
        self._clock = clock or self._timestamp

    async def resume_active(
        self,
        run_id: str,
        context: ExecutionContext,
        *,
        request: RunResumeRequest | None = None,
    ) -> RunResumeExecution:
        index = self._run_store.get(run_id)
        if index is None:
            raise ValueError(f"Run 不存在: {run_id}")
        if request is None:
            resume_request = RunResumeRequest(
                requested_run_id=run_id,
                candidate_run_ids=(run_id,),
                observed_artifacts=index.artifacts,
                rehydrated_from_store=True,
            )
        elif not request.observed_artifacts and index.artifacts:
            resume_request = replace(
                request,
                observed_artifacts=index.artifacts,
                rehydrated_from_store=True,
            )
        else:
            resume_request = request
        decision = RunResumeResolver.resolve(index, resume_request)
        if decision.disposition is not RunResumeDisposition.ALLOW:
            return RunResumeExecution(decision, None, index)

        workflow_id = decision.selected_workflow_id
        checkpoint_id = decision.selected_checkpoint_id
        workflow = self._workflows.get(workflow_id or "")
        if workflow is None:
            blocked = self._blocked(
                index,
                RunResumeReasonCode.RUN_INDEX_INCONSISTENT,
                "active Workflow definition 不存在",
            )
            return RunResumeExecution(blocked, None, index)
        summary = index.workflow(workflow.id)
        if summary is None:
            blocked = self._blocked(
                index,
                RunResumeReasonCode.RUN_INDEX_INCONSISTENT,
                "active Workflow summary 不存在",
            )
            return RunResumeExecution(blocked, None, index)

        run_hydration = hydrate_run_artifacts(index.artifacts, context)
        required_artifact_ids = {
            requirement.artifact_id
            for requirement in summary.required_artifacts
        }
        required_types = {
            artifact.artifact_type
            for artifact in index.artifacts
            if artifact.artifact_id in required_artifact_ids
        }
        if required_types.intersection(
            set(run_hydration.missing_types) | set(run_hydration.mismatched_types)
        ):
            blocked = self._blocked(
                index,
                RunResumeReasonCode.UPSTREAM_ARTIFACT_CHANGED,
                "Run Artifact 无法通过当前文件 digest 校验",
            )
            return RunResumeExecution(blocked, None, index)

        checkpoint = self._checkpoint_store.get(checkpoint_id or "") if checkpoint_id else None
        if checkpoint is None and not checkpoint_id:
            latest_for_workflow = getattr(
                self._checkpoint_store,
                "latest_for_workflow",
                None,
            )
            latest = (
                latest_for_workflow(
                    run_id,
                    workflow.id,
                    activation_attempt_id=summary.activation_attempt_id,
                )
                if callable(latest_for_workflow)
                else None
            )
            if latest is not None:
                checkpoint = latest
                checkpoint_id = latest.checkpoint_id
                decision = replace(
                    decision,
                    selected_checkpoint_id=checkpoint_id,
                    evidence=decision.evidence + (
                        self._lineage_evidence(str(checkpoint_id)),
                    ),
                )
        if checkpoint_id and checkpoint is None:
            blocked = self._blocked(
                index,
                RunResumeReasonCode.CHECKPOINT_NOT_FOUND,
                "active Workflow 的 checkpoint 不存在",
            )
            return RunResumeExecution(blocked, None, index)
        if checkpoint is not None and (
            checkpoint.run_id != run_id or checkpoint.workflow_id != workflow.id
        ):
            blocked = self._blocked(
                index,
                RunResumeReasonCode.ACTIVE_CHECKPOINT_MISMATCH,
                "checkpoint 的 Run/Workflow identity 与 Run index 不一致",
            )
            return RunResumeExecution(blocked, None, index)
        if (
            checkpoint is not None
            and summary.activation_attempt_id
            and checkpoint.activation_attempt_id != summary.activation_attempt_id
        ):
            blocked = self._blocked(
                index,
                RunResumeReasonCode.ACTIVE_CHECKPOINT_MISMATCH,
                "checkpoint activation attempt 与 active Workflow 不一致",
            )
            return RunResumeExecution(blocked, None, index)

        if checkpoint is None and (
            summary.status is not RunWorkflowStatus.RUNNING
            or not summary.activation_attempt_id
        ):
            blocked = self._blocked(
                index,
                RunResumeReasonCode.CHECKPOINT_NOT_FOUND,
                "active Workflow 尚未拥有 checkpoint 或 activation attempt",
            )
            return RunResumeExecution(blocked, None, index)

        plan_version = checkpoint.plan_version if checkpoint is not None else "1.0"
        target_summary = (
            checkpoint.target_summary
            if checkpoint is not None
            else workflow.description or workflow.id
        )
        resume_context = None
        if checkpoint is not None:
            resume_context = ResumeContext(
                workflow_id=workflow.id,
                workflow_version=workflow.version,
                plan_version=plan_version,
                requested_action=decision.workflow_action,
                requested_target=target_summary,
                candidate_run_ids=(run_id,),
                requested_stage_id=checkpoint.active_stage_id,
                requested_task_id=checkpoint.active_task_id,
                stage_idempotent=summary.active_stage_idempotent,
                external_state_evidence=resume_request.external_state_evidence,
            )
        execution_checkpoint_store = self._checkpoint_store
        durable_buffer: CheckpointStagingBuffer | None = None
        if self._durable_store is not None:
            durable_buffer = self._build_durable_checkpoint_buffer(
                workflow.id,
                summary.activation_attempt_id,
            )
            execution_checkpoint_store = durable_buffer
        assert execution_checkpoint_store is not None

        prepared_operation = None
        if self._durable_store is not None:
            prepared_operation = self._prepare_durable_workflow(
                workflow,
                summary.activation_attempt_id,
                checkpoint_id=checkpoint_id or "start",
                target_summary=target_summary,
                requested_action=decision.workflow_action,
            )

        checkpoint_request = WorkflowCheckpointRequest(
            store=execution_checkpoint_store,
            run_id=run_id,
            session_id=checkpoint.session_id if checkpoint else index.session_id,
            conversation_id=(
                checkpoint.conversation_id if checkpoint else index.conversation_id
            ),
            user_scope=checkpoint.user_scope if checkpoint else index.user_scope,
            plan_version=plan_version,
            target_summary=target_summary,
            activation_attempt_id=summary.activation_attempt_id,
            checkpoint=checkpoint,
            resume_context=resume_context,
            external_state_evidence=resume_request.external_state_evidence,
        )
        result = await self._workflow_executor.execute(
            workflow,
            context,
            checkpoint_request=checkpoint_request,
        )
        updated_index = self._record_result(
            index,
            result,
            checkpoint_store=execution_checkpoint_store,
        )
        if self._durable_store is not None:
            assert durable_buffer is not None
            if prepared_operation is not None:
                updated_index = self._finalize_durable_workflow(
                    index,
                    updated_index,
                    result,
                    prepared_operation,
                    durable_buffer,
                    workflow_id=workflow.id,
                )
        elif updated_index is not index:
            assert self._run_store is not None
            self._run_store.save(updated_index)
        return RunResumeExecution(decision, result, updated_index)

    async def execute_or_resume(
        self,
        run_id: str,
        context_factory: Callable[[Workflow], ExecutionContext],
        *,
        attempt_id: str,
        request: RunResumeRequest | None = None,
    ) -> RunResumeExecution:
        """Activate the next eligible Workflow, or resume the existing active one.

        Activation is committed by the durable Store before this method calls
        ``WorkflowExecutor``.  In SQLite mode the activation transaction also
        publishes the initial Checkpoint; a process restart therefore resumes
        from a persisted active lineage rather than an in-memory placeholder.
        """
        index = self._run_store.get(run_id)
        if index is None:
            raise ValueError(f"Run 不存在: {run_id}")
        if index.active_workflow_id:
            workflow = self._workflows.get(index.active_workflow_id)
            if workflow is None:
                blocked = self._blocked(
                    index,
                    RunResumeReasonCode.RUN_INDEX_INCONSISTENT,
                    "active Workflow definition 不存在",
                )
                return RunResumeExecution(blocked, None, index)
            return await self.resume_active(
                run_id,
                context_factory(workflow),
                request=request,
            )

        pending = next(
            (workflow_id for workflow_id in index.workflow_sequence
             if workflow_id in index.pending_workflow_ids),
            None,
        )
        if pending is None:
            return RunResumeExecution(self._completed(index), None, index)
        workflow = self._workflows.get(pending)
        if workflow is None:
            blocked = self._blocked(
                index,
                RunResumeReasonCode.RUN_INDEX_INCONSISTENT,
                "pending Workflow definition 不存在",
            )
            return RunResumeExecution(blocked, None, index)
        initial_checkpoint = (
            self._initial_activation_checkpoint(index, workflow, attempt_id)
            if self._durable_store is not None
            else None
        )
        try:
            if initial_checkpoint is not None:
                assert self._durable_store is not None
                activated = self._durable_store.activate_workflow(
                    pending,
                    expected_revision=index.revision,
                    attempt_id=attempt_id,
                    initial_checkpoint=initial_checkpoint,
                )
            else:
                activated = self._run_store.activate_workflow(
                    run_id,
                    pending,
                    expected_revision=index.revision,
                    attempt_id=attempt_id,
                )
        except RunResumeActivationError as exc:
            blocked_reason = {
                "UPSTREAM_WORKFLOW_INCOMPLETE": RunResumeReasonCode.UPSTREAM_WORKFLOW_INCOMPLETE,
                "UPSTREAM_ARTIFACT_MISSING": RunResumeReasonCode.UPSTREAM_ARTIFACT_MISSING,
                "UPSTREAM_ARTIFACT_CHANGED": RunResumeReasonCode.UPSTREAM_ARTIFACT_CHANGED,
                "REVISION_CONFLICT": RunResumeReasonCode.RUN_INDEX_INCONSISTENT,
            }.get(exc.code, RunResumeReasonCode.RUN_INDEX_INCONSISTENT)
            blocked = self._blocked(index, blocked_reason, str(exc))
            return RunResumeExecution(blocked, None, index)

        return await self.resume_active(
            run_id,
            context_factory(workflow),
            request=request,
        )

    def _initial_activation_checkpoint(
        self,
        index: RunResumeIndex,
        workflow: Workflow,
        attempt_id: str,
    ) -> RunCheckpoint:
        stages = workflow.topological_sort()
        first_stage = stages[0] if stages else None
        now = self._clock()
        return RunCheckpoint(
            run_id=index.run_id,
            checkpoint_id=f"{index.run_id}:{workflow.id}:{attempt_id}:initial",
            parent_checkpoint_id=None,
            sequence_number=0,
            session_id=index.session_id,
            conversation_id=index.conversation_id,
            user_scope=index.user_scope,
            workflow_id=workflow.id,
            workflow_version=workflow.version,
            plan_version="1.0",
            active_stage_id=first_stage.id if first_stage else "",
            active_task_id=first_stage.id if first_stage else "",
            status=CheckpointStatus.RUNNING,
            execution_plan=WorkflowExecutor._workflow_plan_fact(workflow),
            target_summary=workflow.description or workflow.id,
            activation_attempt_id=attempt_id,
            verifier_status="VERIFIED",
            created_at=now,
            updated_at=now,
        )

    def _build_durable_checkpoint_buffer(
        self,
        workflow_id: str,
        activation_attempt_id: str,
    ) -> CheckpointStagingBuffer:
        if self._durable_store is None:
            raise RuntimeError("durable store view is not configured")
        buffer = CheckpointStagingBuffer()
        for checkpoint in self._durable_store.checkpoint_history(
            workflow_id=workflow_id,
            activation_attempt_id=activation_attempt_id,
        ):
            buffer.save(checkpoint)
        return buffer

    def _prepare_durable_workflow(
        self,
        workflow: Workflow,
        activation_attempt_id: str,
        *,
        checkpoint_id: str,
        target_summary: str,
        requested_action: Any,
    ):
        if self._durable_store is None:
            raise RuntimeError("durable store view is not configured")
        request_digest = fact_digest({
            "run_id": self._durable_store.run_id,
            "workflow_id": workflow.id,
            "workflow_version": workflow.version,
            "activation_attempt_id": activation_attempt_id,
            "checkpoint_id": checkpoint_id,
            "target_summary": target_summary,
            "requested_action": (
                requested_action.value
                if hasattr(requested_action, "value")
                else str(requested_action or "")
            ),
        })
        return self._durable_store.prepare_operation(
            idempotency_key=(
                f"workflow:{self._durable_store.run_id}:"
                f"{workflow.id}:{activation_attempt_id}:{checkpoint_id}"
            ),
            operation_type="workflow.execute",
            request_digest=request_digest,
            external_reference=target_summary,
        )

    def _finalize_durable_workflow(
        self,
        original_index: RunResumeIndex,
        updated_index: RunResumeIndex,
        result: ExecutionResult,
        prepared_operation: Any,
        checkpoint_store: CheckpointStagingBuffer,
        *,
        workflow_id: str,
    ) -> RunResumeIndex:
        if self._durable_store is None:
            return updated_index
        checkpoint_id = str((result.metadata or {}).get("checkpoint_id", ""))
        checkpoint = checkpoint_store.get(checkpoint_id) if checkpoint_id else None
        if checkpoint is None:
            return original_index
        unresolved = tuple(
            (result.metadata or {}).get("unresolved_resume_diagnostics", ()) or ()
        )
        if checkpoint.verifier_status.upper() != "VERIFIED" or unresolved:
            # A failed/unknown effect stays represented by the PREPARED intent;
            # it cannot be published as a successful durable execution fact.
            return original_index

        persisted_ids = {
            item.checkpoint_id
            for item in self._durable_store.checkpoint_history(
                workflow_id=workflow_id,
                activation_attempt_id=checkpoint.activation_attempt_id,
            )
        }
        checkpoint_chain = tuple(
            item
            for item in checkpoint_store.history(self._durable_store.run_id)
            if item.workflow_id == workflow_id and item.checkpoint_id not in persisted_ids
        )
        if not checkpoint_chain or checkpoint_chain[-1].checkpoint_id != checkpoint.checkpoint_id:
            return original_index

        artifact_facts = tuple(
            ArtifactCommitFact(
                artifact_id=artifact.artifact_id,
                artifact_type=artifact.artifact_type or "artifact",
                reference=artifact.reference,
                digest=artifact.digest,
                producer_workflow_id=artifact.producer_workflow_id,
                producer_stage_id=artifact.producer_stage_id or "workflow-terminal",
                exists=artifact.exists,
                verified=artifact.verified,
                verification_evidence_digest=fact_digest(artifact.to_dict()),
                producer_task_id=artifact.producer_task_id,
            )
            for artifact in updated_index.artifacts
            if artifact.producer_workflow_id == workflow_id
        )
        head = self._durable_store.head()
        next_index = replace(
            updated_index,
            revision=head.current_revision + 1,
            parent_digest=head.current_digest,
            store_generation=self._durable_store.store_generation,
        )
        result_digest = fact_digest({
            "success": result.success,
            "outputs": result.outputs,
            "error": result.error,
            "metadata": result.metadata or {},
        })
        bundle = FinalizationBundle(
            tenant_id=self._durable_store.tenant_id,
            session_id=self._durable_store.session_id,
            run_id=self._durable_store.run_id,
            workflow_id=workflow_id,
            request_id=self._durable_store.request_id,
            writer_id=self._durable_store.writer_id,
            fence_epoch=self._durable_store.fence_epoch,
            expected_revision=prepared_operation.prepared_revision,
            expected_parent_digest=head.current_digest,
            idempotency_key=prepared_operation.idempotency_key,
            operation_type=prepared_operation.operation_type,
            request_digest=prepared_operation.request_digest,
            checkpoint=checkpoint,
            checkpoint_chain=checkpoint_chain,
            artifacts=artifact_facts,
            next_run_index=next_index,
            external_result_digest=result_digest,
            verifier_status="VERIFIED",
        )
        self._durable_store.finalize_bundle(bundle)
        return next_index

    def _record_result(
        self,
        index: RunResumeIndex,
        result: ExecutionResult,
        *,
        checkpoint_store: CheckpointStore | None = None,
    ) -> RunResumeIndex:
        checkpoint_store = checkpoint_store or self._checkpoint_store
        if checkpoint_store is None:
            return index
        metadata = result.metadata or {}
        checkpoint_id = str(metadata.get("checkpoint_id", ""))
        if not checkpoint_id:
            return index
        checkpoint_status = str(metadata.get("checkpoint_status", ""))
        completion_gate_ok = bool(metadata.get("terminal_outputs_verified", True))
        unresolved = tuple(metadata.get("unresolved_resume_diagnostics", ()) or ())
        if (
            checkpoint_status == CheckpointStatus.COMPLETED.value
            and result.success
            and completion_gate_ok
            and not unresolved
        ):
            checkpoint = checkpoint_store.get(checkpoint_id)
            active_workflow_id = index.active_workflow_id
            checkpoint_verified = (
                checkpoint is not None
                and checkpoint.verifier_status == "VERIFIED"
            )
            published_artifacts = tuple(
                RunArtifactFact(
                    artifact_id=artifact.artifact_id,
                    producer_workflow_id=active_workflow_id,
                    digest=artifact.digest,
                    exists=artifact.exists,
                    verified=(
                        artifact.exists
                        and checkpoint_verified
                    ),
                    artifact_type=artifact.artifact_type,
                    reference=artifact.reference,
                    encoding=dict(artifact.metadata).get("encoding", "utf-8"),
                    producer_stage_id=dict(artifact.metadata).get(
                        "producer_stage_id", ""
                    ),
                    producer_task_id=dict(artifact.metadata).get(
                        "producer_task_id", ""
                    ),
                )
                for artifact in (checkpoint.artifacts if checkpoint is not None else ())
            )
            return index.complete_active(
                checkpoint_id,
                updated_at=self._clock(),
                parent_digest=run_index_digest(index),
                artifacts=published_artifacts,
            )
        if (
            checkpoint_status == CheckpointStatus.COMPLETED.value
            and (not completion_gate_ok or unresolved)
        ):
            return index.with_active_checkpoint(
                checkpoint_id,
                status=RunWorkflowStatus.FAILED_RECOVERABLE,
                verifier_status="FAILED",
                updated_at=self._clock(),
                parent_digest=run_index_digest(index),
            )
        status_map = {
            CheckpointStatus.SUSPENDED.value: RunWorkflowStatus.SUSPENDED,
            CheckpointStatus.WAITING_USER.value: RunWorkflowStatus.WAITING_USER,
            CheckpointStatus.FAILED_RECOVERABLE.value: RunWorkflowStatus.FAILED_RECOVERABLE,
            CheckpointStatus.RUNNING.value: RunWorkflowStatus.RUNNING,
        }
        status = status_map.get(
            checkpoint_status,
            RunWorkflowStatus.FAILED_RECOVERABLE,
        )
        return index.with_active_checkpoint(
            checkpoint_id,
            status=status,
            verifier_status="VERIFIED" if result.success else "FAILED",
            updated_at=self._clock(),
            parent_digest=run_index_digest(index),
        )

    @staticmethod
    def _completed(index: RunResumeIndex) -> RunResumeDecision:
        from agent.checkpoint import RuntimeEvidence

        return RunResumeDecision(
            disposition=RunResumeDisposition.REJECT,
            run_id=index.run_id,
            workflow_action=None,
            selected_workflow_id=None,
            selected_checkpoint_id=None,
            skipped_workflow_ids=index.completed_workflow_ids,
            remaining_workflow_ids=(),
            resulting_status="COMPLETED",
            reason_code=RunResumeReasonCode.RUN_COMPLETED,
            evidence=(RuntimeEvidence(
                source="run_resume_coordinator",
                kind="run_lifecycle",
                expected="no pending Workflow",
                observed="completed",
                status="VERIFIED",
            ),),
        )

    @staticmethod
    def _lineage_evidence(checkpoint_id: str):
        from agent.checkpoint import RuntimeEvidence

        return RuntimeEvidence(
            source="run_resume_coordinator",
            kind="checkpoint_lineage_fallback",
            expected="latest active Workflow checkpoint",
            observed=checkpoint_id,
            status="VERIFIED",
        )

    @staticmethod
    def _blocked(
        index: RunResumeIndex,
        reason: RunResumeReasonCode,
        detail: str,
    ) -> RunResumeDecision:
        from agent.checkpoint import RuntimeEvidence

        return RunResumeDecision(
            disposition=RunResumeDisposition.REJECT,
            run_id=index.run_id,
            workflow_action=None,
            selected_workflow_id=None,
            selected_checkpoint_id=None,
            skipped_workflow_ids=(),
            remaining_workflow_ids=index.pending_workflow_ids,
            resulting_status="REJECTED",
            reason_code=reason,
            evidence=(RuntimeEvidence(
                source="run_resume_coordinator",
                kind="integration_boundary",
                observed=detail,
                status="MISMATCH",
            ),),
        )

    @staticmethod
    def _timestamp() -> str:
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = ["RunResumeCoordinator", "RunResumeExecution"]
