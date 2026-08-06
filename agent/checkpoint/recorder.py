"""Workflow-facing Checkpoint recording primitives for v2.2B.

This module deliberately knows only about facts and generic mappings.  It does
not import Workflow, Executor, Tool, or any external service.  The existing
WorkflowExecutor supplies those facts and consumes the resulting
``ResumeDecision``.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Iterable, Mapping, cast

from .compatibility import CompatibilityRegistry
from .contracts import (
    ArtifactSnapshot,
    ExternalStateGuard,
    FailureEventSnapshot,
    ResumeContext,
    ResumeDecision,
    RunCheckpoint,
    RuntimeEvidence,
    TaskEffectRecord,
)
from .lifecycle import advance_checkpoint, append_checkpoint
from .reason_codes import CheckpointStatus, ResumeAction, SideEffectState
from .store import CheckpointStore


Clock = Callable[[], str]
CheckpointIdFactory = Callable[[str], str]


def utc_timestamp() -> str:
    """Return a stable, timezone-aware timestamp for a new fact snapshot."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def new_checkpoint_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


def json_fact(value: Any) -> Any:
    """Convert a runtime value to JSON-like facts without retaining live objects."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return json_fact(value.value)
    if isinstance(value, Mapping):
        return {
            str(key): json_fact(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [json_fact(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [json_fact(item) for item in sorted(value, key=str)]
    # Preserve the fact that an opaque value existed, never the object itself.
    return {"opaque_type": type(value).__name__}


def fact_digest(value: Any) -> str:
    payload = json.dumps(
        json_fact(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def snapshot_artifacts(artifacts: Iterable[Any]) -> tuple[ArtifactSnapshot, ...]:
    """Project live Artifact-like values into immutable references and digests."""
    snapshots: list[ArtifactSnapshot] = []
    for artifact in artifacts:
        metadata = getattr(artifact, "metadata", None)
        metadata_pairs: tuple[tuple[str, str], ...] = ()
        if isinstance(metadata, Mapping):
            metadata_pairs = tuple(
                (str(key), str(value))
                for key, value in sorted(metadata.items(), key=lambda pair: str(pair[0]))
            )
        snapshots.append(ArtifactSnapshot(
            artifact_id=str(getattr(artifact, "id", "")),
            artifact_type=str(getattr(artifact, "type", "")),
            digest=fact_digest(getattr(artifact, "content", "")),
            exists=True,
            reference=str(getattr(artifact, "storage_uri", "") or ""),
            metadata=metadata_pairs,
        ))
    return tuple(snapshots)


def effect_state_for_task(
    task_verb: str,
    *,
    success: bool,
    metadata: Mapping[str, Any] | None = None,
) -> SideEffectState:
    """Derive a conservative side-effect fact from an executor result."""
    raw = (metadata or {}).get("side_effect_state")
    if raw is not None:
        try:
            return SideEffectState(raw)
        except ValueError:
            # Invalid executor metadata must not become an unsafe allow.
            return SideEffectState.UNKNOWN
    if str(task_verb).lower() in {"write", "modify", "delete", "move", "execute"}:
        return SideEffectState.COMMITTED if success else SideEffectState.FAILED_BEFORE_COMMIT
    return SideEffectState.NONE


def failure_snapshot(
    *,
    event_id: str,
    stage_id: str,
    error: str,
    metadata: Mapping[str, Any] | None = None,
) -> FailureEventSnapshot:
    metadata = metadata or {}
    return FailureEventSnapshot(
        event_id=event_id,
        layer="workflow",
        symptom=str(metadata.get("symptom", "stage_execution_failed")),
        failure=str(error),
        evidence=(RuntimeEvidence(
            source="workflow_executor",
            kind="stage_result",
            expected="success",
            observed="failed",
            status="FAILED",
            detail=stage_id,
        ),),
    )


@dataclass(frozen=True)
class WorkflowCheckpointRequest:
    """Optional runtime contract supplied to ``WorkflowExecutor``."""

    store: CheckpointStore
    run_id: str
    session_id: str = "default-session"
    conversation_id: str = "default-conversation"
    user_scope: str = "default-user"
    plan_version: str = "1.0"
    target_summary: str = ""
    activation_attempt_id: str = ""
    checkpoint: RunCheckpoint | None = None
    resume_context: ResumeContext | None = None
    external_state_evidence: tuple[ExternalStateGuard, ...] = ()
    compatibility_registry: CompatibilityRegistry = field(
        default_factory=CompatibilityRegistry
    )
    interrupt_after_stage_id: str | None = None
    clock: Clock = utc_timestamp
    checkpoint_id_factory: CheckpointIdFactory = new_checkpoint_id

    def __post_init__(self) -> None:
        for name in (
            "run_id", "session_id", "conversation_id", "user_scope", "plan_version"
        ):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"WorkflowCheckpointRequest.{name} 不能为空")
        if self.checkpoint is not None and self.checkpoint.run_id != self.run_id:
            raise ValueError("resume checkpoint 的 run_id 必须与 request 一致")
        object.__setattr__(
            self,
            "external_state_evidence",
            tuple(
                item if isinstance(item, ExternalStateGuard)
                else ExternalStateGuard.from_dict(cast(Mapping[str, Any], item))
                for item in (self.external_state_evidence or ())
            ),
        )


class CheckpointRecorder:
    """Append-only fact recorder; it never chooses a resume action."""

    def __init__(self, request: WorkflowCheckpointRequest) -> None:
        self.request = request
        self.store = request.store
        self.current: RunCheckpoint | None = request.checkpoint

    def _new_id(self, label: str) -> str:
        return self.request.checkpoint_id_factory(
            f"{self.request.run_id}-{label}"
        )

    def _save_transition(
        self,
        checkpoint: RunCheckpoint,
        *,
        status: CheckpointStatus,
        **changes: Any,
    ) -> RunCheckpoint:
        checkpoint_id = self._new_id("cp")
        if status is checkpoint.status:
            child = append_checkpoint(
                checkpoint,
                checkpoint_id=checkpoint_id,
                updated_at=self.request.clock(),
                **changes,
            )
        else:
            child = advance_checkpoint(
                checkpoint,
                status,
                checkpoint_id=checkpoint_id,
                updated_at=self.request.clock(),
                **changes,
            )
        self.current = self.store.save(child)
        return self.current

    def start(
        self,
        *,
        workflow_id: str,
        workflow_version: str,
        active_stage_id: str,
        active_task_id: str,
        execution_plan: Mapping[str, Any],
        target_summary: str = "",
        activation_attempt_id: str = "",
    ) -> RunCheckpoint:
        if self.current is not None:
            raise ValueError("CheckpointRecorder 已经绑定一个既有 Run")
        initial = RunCheckpoint(
            run_id=self.request.run_id,
            checkpoint_id=self._new_id("initial"),
            parent_checkpoint_id=None,
            sequence_number=0,
            session_id=self.request.session_id,
            conversation_id=self.request.conversation_id,
            user_scope=self.request.user_scope,
            workflow_id=workflow_id,
            workflow_version=workflow_version,
            plan_version=self.request.plan_version,
            active_stage_id=active_stage_id,
            active_task_id=active_task_id,
            status=CheckpointStatus.CREATED,
            execution_plan=json_fact(execution_plan),
            target_summary=target_summary or self.request.target_summary,
            activation_attempt_id=(
                activation_attempt_id or self.request.activation_attempt_id
            ),
            created_at=self.request.clock(),
            updated_at=self.request.clock(),
        )
        self.current = self.store.save(initial)
        return self._save_transition(self.current, status=CheckpointStatus.RUNNING)

    def resume(self, decision: ResumeDecision) -> RunCheckpoint:
        current = self.current
        if current is None:
            raise ValueError("没有可恢复的 Checkpoint")
        if decision.run_id != current.run_id or decision.checkpoint_id != current.checkpoint_id:
            raise ValueError("ResumeDecision 与 latest Checkpoint 不匹配")
        if decision.action not in {
            ResumeAction.RESUME_EXACT,
            ResumeAction.REPLAY_FROM_STAGE,
        }:
            raise ValueError(
                f"v2.2B 不消费 ResumeAction: {decision.action.value if decision.action else None}"
            )

        if current.status in {
            CheckpointStatus.FAILED_RECOVERABLE,
            CheckpointStatus.WAITING_USER,
        }:
            current = self._save_transition(current, status=CheckpointStatus.SUSPENDED)
        if current.status is CheckpointStatus.SUSPENDED:
            return self._save_transition(
                current,
                status=CheckpointStatus.RUNNING,
                proposed_next_action=decision.action,
            )
        if current.status is CheckpointStatus.RUNNING:
            return self._save_transition(
                current,
                status=CheckpointStatus.RUNNING,
                proposed_next_action=decision.action,
            )
        raise ValueError(f"Checkpoint 状态不可恢复: {current.status.value}")

    def record_stage(
        self,
        *,
        stage_id: str,
        task_id: str,
        execution_plan: Mapping[str, Any],
        success: bool,
        result_error: str,
        result_metadata: Mapping[str, Any] | None,
        task_verb: str,
        next_stage_id: str,
        next_task_id: str,
        artifacts: Iterable[Any],
        target_summary: str = "",
    ) -> RunCheckpoint:
        current = self.current
        if current is None:
            raise ValueError("必须先 start 或 resume CheckpointRecorder")
        metadata = result_metadata or {}
        effect_state = effect_state_for_task(
            task_verb,
            success=success,
            metadata=metadata,
        )
        idempotency_key = f"{current.run_id}:{task_id}"
        effect = TaskEffectRecord(
            task_id=task_id,
            tool_name=str(metadata.get("executor", "")),
            operation_type=str(task_verb),
            idempotency_key=idempotency_key,
            effect_state=effect_state,
            external_reference=str(metadata.get("external_reference", "")),
            evidence=(RuntimeEvidence(
                source="workflow_executor",
                kind="task_effect",
                expected="committed" if success else "not_committed",
                observed=effect_state.value,
                status="VERIFIED" if effect_state is not SideEffectState.UNKNOWN else "UNKNOWN",
                detail=task_id,
            ),),
        )
        effects = tuple(
            item for item in current.task_effect_records if item.task_id != task_id
        ) + (effect,)
        keys = tuple(dict.fromkeys((*current.idempotency_keys, idempotency_key)))
        completed_stages = current.completed_stage_ids
        completed_tasks = current.completed_task_ids
        if success:
            completed_stages = tuple(dict.fromkeys((*completed_stages, stage_id)))
            completed_tasks = tuple(dict.fromkeys((*completed_tasks, task_id)))

        failure = None
        status = CheckpointStatus.RUNNING
        if not success:
            failure = failure_snapshot(
                event_id=self._new_id("failure"),
                stage_id=stage_id,
                error=result_error,
                metadata=metadata,
            )
            status = (
                CheckpointStatus.WAITING_USER
                if effect_state in {
                    SideEffectState.UNKNOWN,
                    SideEffectState.STARTED,
                    SideEffectState.FAILED_AFTER_COMMIT,
                }
                else CheckpointStatus.FAILED_RECOVERABLE
            )

        return self._save_transition(
            current,
            status=status,
            active_stage_id=next_stage_id if success else stage_id,
            active_task_id=next_task_id if success else task_id,
            execution_plan=json_fact(execution_plan),
            target_summary=target_summary or current.target_summary,
            completed_stage_ids=completed_stages,
            completed_task_ids=completed_tasks,
            artifacts=snapshot_artifacts(artifacts),
            verifier_status=("VERIFIED" if success else "FAILED"),
            failure_event=failure,
            task_effect_records=effects,
            idempotency_keys=keys,
        )

    def suspend(self, *, reason: str = "explicit_interruption") -> RunCheckpoint:
        current = self.current
        if current is None:
            raise ValueError("没有可暂停的 Checkpoint")
        return self._save_transition(
            current,
            status=CheckpointStatus.SUSPENDED,
            invalidation_reasons=tuple(current.invalidation_reasons),
            runtime_evidence=tuple(current.runtime_evidence) + (RuntimeEvidence(
                source="workflow_executor",
                kind="suspension",
                observed=reason,
                status="VERIFIED",
            ),),
        )

    def complete(self, *, artifacts: Iterable[Any], summary: str = "") -> RunCheckpoint:
        current = self.current
        if current is None:
            raise ValueError("没有可完成的 Checkpoint")
        return self._save_transition(
            current,
            status=CheckpointStatus.COMPLETED,
            active_stage_id="",
            active_task_id="",
            artifacts=snapshot_artifacts(artifacts),
            target_summary=summary or current.target_summary,
            verifier_status="VERIFIED",
        )


def checkpoint_result_metadata(
    checkpoint: RunCheckpoint | None,
    decision: ResumeDecision | None = None,
) -> dict[str, Any]:
    """Return JSON-like runtime metadata for an ExecutionResult."""
    if checkpoint is None:
        return {}
    metadata: dict[str, Any] = {
        "run_id": checkpoint.run_id,
        "checkpoint_id": checkpoint.checkpoint_id,
        "checkpoint_sequence": checkpoint.sequence_number,
        "checkpoint_status": checkpoint.status.value,
    }
    if decision is not None:
        metadata["resume_decision"] = decision.to_dict()
    return metadata


__all__ = [
    "CheckpointRecorder",
    "WorkflowCheckpointRequest",
    "checkpoint_result_metadata",
    "effect_state_for_task",
    "fact_digest",
    "failure_snapshot",
    "json_fact",
    "new_checkpoint_id",
    "snapshot_artifacts",
    "utc_timestamp",
]
