"""Immutable v2.2A Run Checkpoint data contracts.

The objects in this module record facts only.  They never query external
systems and never decide how a run should resume; that boundary belongs to
``agent.checkpoint.validator``.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Optional, cast

from .reason_codes import (
    CheckpointStatus,
    GuardStatus,
    ResumeAction,
    ResumeDisposition,
    ResumeReasonCode,
    SideEffectState,
)


def _freeze(value: Any) -> Any:
    """Recursively freeze JSON-like values for immutable snapshots."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return MappingProxyType({
            str(key): _freeze(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        })
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set):
        return frozenset(_freeze(item) for item in value)
    raise TypeError(
        f"Checkpoint execution_plan 只能包含 JSON-like 值，收到 {type(value).__name__}"
    )


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, frozenset)):
        return [_thaw(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    return value


def _tuple_strings(values: Any) -> tuple[str, ...]:
    return tuple(str(value) for value in (values or ()) if str(value).strip())


def _tuple_pairs(values: Any) -> tuple[tuple[str, str], ...]:
    if isinstance(values, Mapping):
        values = values.items()
    return tuple(
        (str(pair[0]), str(pair[1]))
        for pair in (values or ())
        if isinstance(pair, (list, tuple)) and len(pair) == 2
    )


@dataclass(frozen=True)
class RuntimeEvidence:
    """A deterministic fact supplied to a resume decision."""

    source: str
    kind: str
    expected: str = ""
    observed: str = ""
    status: str = "VERIFIED"
    detail: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "source": self.source,
            "kind": self.kind,
            "expected": self.expected,
            "observed": self.observed,
            "status": self.status,
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RuntimeEvidence":
        return cls(
            source=str(value.get("source", "")),
            kind=str(value.get("kind", "")),
            expected=str(value.get("expected", "")),
            observed=str(value.get("observed", "")),
            status=str(value.get("status", "VERIFIED")),
            detail=str(value.get("detail", "")),
        )


@dataclass(frozen=True)
class ExternalStateGuard:
    """A previously collected observation about an external resource."""

    resource_id: str
    guard_type: str
    expected_value: str = ""
    observed_value: str = ""
    checked_at: str = ""
    status: GuardStatus = GuardStatus.VERIFIED

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", GuardStatus(self.status))

    def to_dict(self) -> dict[str, str]:
        return {
            "resource_id": self.resource_id,
            "guard_type": self.guard_type,
            "expected_value": self.expected_value,
            "observed_value": self.observed_value,
            "checked_at": self.checked_at,
            "status": self.status.value,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ExternalStateGuard":
        return cls(
            resource_id=str(value.get("resource_id", "")),
            guard_type=str(value.get("guard_type", "")),
            expected_value=str(value.get("expected_value", "")),
            observed_value=str(value.get("observed_value", "")),
            checked_at=str(value.get("checked_at", "")),
            status=GuardStatus(str(value.get("status", GuardStatus.VERIFIED.value))),
        )


@dataclass(frozen=True)
class TaskEffectRecord:
    """Per-task side-effect fact used to prevent unsafe replay."""

    task_id: str
    tool_name: str = ""
    operation_type: str = ""
    idempotency_key: str = ""
    effect_state: SideEffectState = SideEffectState.NONE
    external_reference: str = ""
    evidence: tuple[RuntimeEvidence, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "effect_state", SideEffectState(self.effect_state))
        object.__setattr__(
            self,
            "evidence",
            tuple(
                item if isinstance(item, RuntimeEvidence)
                else RuntimeEvidence.from_dict(cast(Mapping[str, Any], item))
                for item in (self.evidence or ())
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "tool_name": self.tool_name,
            "operation_type": self.operation_type,
            "idempotency_key": self.idempotency_key,
            "effect_state": self.effect_state.value,
            "external_reference": self.external_reference,
            "evidence": [item.to_dict() for item in self.evidence],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TaskEffectRecord":
        return cls(
            task_id=str(value.get("task_id", "")),
            tool_name=str(value.get("tool_name", "")),
            operation_type=str(value.get("operation_type", "")),
            idempotency_key=str(value.get("idempotency_key", "")),
            effect_state=SideEffectState(
                str(value.get("effect_state", SideEffectState.NONE.value))
            ),
            external_reference=str(value.get("external_reference", "")),
            evidence=tuple(
                RuntimeEvidence.from_dict(item)
                for item in (value.get("evidence", []) or ())
            ),
        )


@dataclass(frozen=True)
class ArtifactSnapshot:
    """Small immutable artifact reference; content stays outside the checkpoint."""

    artifact_id: str
    artifact_type: str
    digest: str = ""
    exists: bool = True
    version: str = ""
    reference: str = ""
    metadata: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", _tuple_pairs(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "artifact_type": self.artifact_type,
            "digest": self.digest,
            "exists": self.exists,
            "version": self.version,
            "reference": self.reference,
            "metadata": {key: value for key, value in self.metadata},
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ArtifactSnapshot":
        return cls(
            artifact_id=str(value.get("artifact_id", "")),
            artifact_type=str(value.get("artifact_type", "")),
            digest=str(value.get("digest", "")),
            exists=bool(value.get("exists", True)),
            version=str(value.get("version", "")),
            reference=str(value.get("reference", "")),
            metadata=_tuple_pairs(value.get("metadata", {})),
        )


@dataclass(frozen=True)
class FailureEventSnapshot:
    """Serializable failure fact without coupling checkpoints to FailBoard."""

    event_id: str = ""
    layer: str = ""
    symptom: str = ""
    failure: str = ""
    evidence: tuple[RuntimeEvidence, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "evidence",
            tuple(
                item if isinstance(item, RuntimeEvidence)
                else RuntimeEvidence.from_dict(cast(Mapping[str, Any], item))
                for item in (self.evidence or ())
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "layer": self.layer,
            "symptom": self.symptom,
            "failure": self.failure,
            "evidence": [item.to_dict() for item in self.evidence],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FailureEventSnapshot":
        return cls(
            event_id=str(value.get("event_id", "")),
            layer=str(value.get("layer", "")),
            symptom=str(value.get("symptom", "")),
            failure=str(value.get("failure", "")),
            evidence=tuple(
                RuntimeEvidence.from_dict(item)
                for item in (value.get("evidence", []) or ())
            ),
        )


@dataclass(frozen=True)
class ResumeContext:
    """Current structured facts supplied to the pure ResumeValidator."""

    workflow_id: str
    workflow_version: str
    plan_version: str
    requested_action: Optional[ResumeAction] = None
    requested_target: str = ""
    candidate_run_ids: tuple[str, ...] = ()
    required_permissions: tuple[str, ...] = ()
    available_permissions: tuple[str, ...] = ()
    requested_stage_id: str = ""
    requested_task_id: str = ""
    stage_idempotent: bool = False
    external_state_evidence: tuple[ExternalStateGuard, ...] = ()

    def __post_init__(self) -> None:
        if self.requested_action is not None:
            object.__setattr__(
                self, "requested_action", ResumeAction(self.requested_action)
            )
        object.__setattr__(self, "candidate_run_ids", _tuple_strings(self.candidate_run_ids))
        object.__setattr__(self, "required_permissions", _tuple_strings(self.required_permissions))
        object.__setattr__(self, "available_permissions", _tuple_strings(self.available_permissions))
        object.__setattr__(
            self,
            "external_state_evidence",
            tuple(
                item if isinstance(item, ExternalStateGuard)
                else ExternalStateGuard.from_dict(cast(Mapping[str, Any], item))
                for item in (self.external_state_evidence or ())
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "workflow_version": self.workflow_version,
            "plan_version": self.plan_version,
            "requested_action": self.requested_action.value if self.requested_action else None,
            "requested_target": self.requested_target,
            "candidate_run_ids": list(self.candidate_run_ids),
            "required_permissions": list(self.required_permissions),
            "available_permissions": list(self.available_permissions),
            "requested_stage_id": self.requested_stage_id,
            "requested_task_id": self.requested_task_id,
            "stage_idempotent": self.stage_idempotent,
            "external_state_evidence": [
                item.to_dict() for item in self.external_state_evidence
            ],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ResumeContext":
        return cls(
            workflow_id=str(value.get("workflow_id", "")),
            workflow_version=str(value.get("workflow_version", "")),
            plan_version=str(value.get("plan_version", "")),
            requested_action=value.get("requested_action"),
            requested_target=str(value.get("requested_target", "")),
            candidate_run_ids=tuple(value.get("candidate_run_ids", []) or ()),
            required_permissions=tuple(value.get("required_permissions", []) or ()),
            available_permissions=tuple(value.get("available_permissions", []) or ()),
            requested_stage_id=str(value.get("requested_stage_id", "")),
            requested_task_id=str(value.get("requested_task_id", "")),
            stage_idempotent=bool(value.get("stage_idempotent", False)),
            external_state_evidence=tuple(
                ExternalStateGuard.from_dict(item)
                for item in (value.get("external_state_evidence", []) or ())
            ),
        )


@dataclass(frozen=True)
class RunCheckpoint:
    """Immutable execution fact snapshot.

    A new checkpoint must be created for every state transition.  This object
    never stores a callable, live tool handle, or mutable execution container.
    """

    run_id: str
    checkpoint_id: str
    parent_checkpoint_id: Optional[str]
    sequence_number: int
    session_id: str
    conversation_id: str
    user_scope: str
    workflow_id: str
    workflow_version: str
    plan_version: str
    active_stage_id: str
    active_task_id: str
    status: CheckpointStatus
    execution_plan: Mapping[str, Any]
    target_summary: str = ""
    completed_stage_ids: tuple[str, ...] = ()
    completed_task_ids: tuple[str, ...] = ()
    artifacts: tuple[ArtifactSnapshot, ...] = ()
    verifier_status: str = "UNKNOWN"
    failure_event: Optional[FailureEventSnapshot] = None
    proposed_next_action: Optional[ResumeAction] = None
    task_effect_records: tuple[TaskEffectRecord, ...] = ()
    idempotency_keys: tuple[str, ...] = ()
    external_state_guards: tuple[ExternalStateGuard, ...] = ()
    invalidation_reasons: tuple[str, ...] = ()
    runtime_evidence: tuple[RuntimeEvidence, ...] = ()
    checkpoint_schema_version: str = "1.0"
    contract_version: str = "v2.2A"
    created_at: str = ""
    updated_at: str = ""
    supersedes_run_id: Optional[str] = None

    def __post_init__(self) -> None:
        required = {
            "run_id": self.run_id,
            "checkpoint_id": self.checkpoint_id,
            "session_id": self.session_id,
            "conversation_id": self.conversation_id,
            "user_scope": self.user_scope,
            "workflow_id": self.workflow_id,
            "workflow_version": self.workflow_version,
            "plan_version": self.plan_version,
        }
        missing = [name for name, value in required.items() if not str(value).strip()]
        if missing:
            raise ValueError(f"RunCheckpoint 缺少必填标识: {', '.join(missing)}")
        if self.sequence_number < 0:
            raise ValueError("sequence_number 不能为负数")
        if self.sequence_number == 0 and self.parent_checkpoint_id is not None:
            raise ValueError("初始 checkpoint 不得有 parent_checkpoint_id")
        if self.sequence_number > 0 and not self.parent_checkpoint_id:
            raise ValueError("非初始 checkpoint 必须有 parent_checkpoint_id")
        if self.parent_checkpoint_id == self.checkpoint_id:
            raise ValueError("checkpoint 不得把自己作为 parent")

        object.__setattr__(self, "status", CheckpointStatus(self.status))
        if not isinstance(self.execution_plan, Mapping):
            raise TypeError("execution_plan 必须是 JSON object")
        object.__setattr__(self, "execution_plan", _freeze(self.execution_plan or {}))
        object.__setattr__(self, "completed_stage_ids", _tuple_strings(self.completed_stage_ids))
        object.__setattr__(self, "completed_task_ids", _tuple_strings(self.completed_task_ids))
        object.__setattr__(self, "idempotency_keys", _tuple_strings(self.idempotency_keys))
        object.__setattr__(self, "invalidation_reasons", _tuple_strings(self.invalidation_reasons))
        if len(set(self.completed_stage_ids)) != len(self.completed_stage_ids):
            raise ValueError("completed_stage_ids 不得重复")
        if len(set(self.completed_task_ids)) != len(self.completed_task_ids):
            raise ValueError("completed_task_ids 不得重复")
        if len(set(self.idempotency_keys)) != len(self.idempotency_keys):
            raise ValueError("idempotency_keys 不得重复")

        object.__setattr__(
            self,
            "artifacts",
            tuple(
                item if isinstance(item, ArtifactSnapshot)
                else ArtifactSnapshot.from_dict(cast(Mapping[str, Any], item))
                for item in (self.artifacts or ())
            ),
        )
        object.__setattr__(
            self,
            "task_effect_records",
            tuple(
                item if isinstance(item, TaskEffectRecord)
                else TaskEffectRecord.from_dict(cast(Mapping[str, Any], item))
                for item in (self.task_effect_records or ())
            ),
        )
        effect_task_ids = [item.task_id for item in self.task_effect_records]
        if len(set(effect_task_ids)) != len(effect_task_ids):
            raise ValueError("task_effect_records 不得重复记录同一 task_id")
        effect_keys = {
            item.idempotency_key
            for item in self.task_effect_records
            if item.idempotency_key
        }
        if not effect_keys.issubset(set(self.idempotency_keys)):
            raise ValueError("task_effect_records 的 idempotency_key 必须登记在 idempotency_keys")
        object.__setattr__(
            self,
            "external_state_guards",
            tuple(
                item if isinstance(item, ExternalStateGuard)
                else ExternalStateGuard.from_dict(cast(Mapping[str, Any], item))
                for item in (self.external_state_guards or ())
            ),
        )
        object.__setattr__(
            self,
            "runtime_evidence",
            tuple(
                item if isinstance(item, RuntimeEvidence)
                else RuntimeEvidence.from_dict(cast(Mapping[str, Any], item))
                for item in (self.runtime_evidence or ())
            ),
        )
        if self.failure_event is not None and not isinstance(
            self.failure_event, FailureEventSnapshot
        ):
            object.__setattr__(
                self, "failure_event", FailureEventSnapshot.from_dict(self.failure_event)
            )
        if self.proposed_next_action is not None:
            object.__setattr__(
                self,
                "proposed_next_action",
                ResumeAction(self.proposed_next_action),
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "checkpoint_id": self.checkpoint_id,
            "parent_checkpoint_id": self.parent_checkpoint_id,
            "sequence_number": self.sequence_number,
            "session_id": self.session_id,
            "conversation_id": self.conversation_id,
            "user_scope": self.user_scope,
            "workflow_id": self.workflow_id,
            "workflow_version": self.workflow_version,
            "plan_version": self.plan_version,
            "active_stage_id": self.active_stage_id,
            "active_task_id": self.active_task_id,
            "status": self.status.value,
            "execution_plan": _thaw(self.execution_plan),
            "target_summary": self.target_summary,
            "completed_stage_ids": list(self.completed_stage_ids),
            "completed_task_ids": list(self.completed_task_ids),
            "artifacts": [item.to_dict() for item in self.artifacts],
            "verifier_status": self.verifier_status,
            "failure_event": self.failure_event.to_dict() if self.failure_event else None,
            "proposed_next_action": (
                self.proposed_next_action.value if self.proposed_next_action else None
            ),
            "task_effect_records": [item.to_dict() for item in self.task_effect_records],
            "idempotency_keys": list(self.idempotency_keys),
            "external_state_guards": [item.to_dict() for item in self.external_state_guards],
            "invalidation_reasons": list(self.invalidation_reasons),
            "runtime_evidence": [item.to_dict() for item in self.runtime_evidence],
            "checkpoint_schema_version": self.checkpoint_schema_version,
            "contract_version": self.contract_version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "supersedes_run_id": self.supersedes_run_id,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RunCheckpoint":
        return cls(
            run_id=str(value.get("run_id", "")),
            checkpoint_id=str(value.get("checkpoint_id", "")),
            parent_checkpoint_id=value.get("parent_checkpoint_id"),
            sequence_number=int(value.get("sequence_number", 0)),
            session_id=str(value.get("session_id", "")),
            conversation_id=str(value.get("conversation_id", "")),
            user_scope=str(value.get("user_scope", "")),
            workflow_id=str(value.get("workflow_id", "")),
            workflow_version=str(value.get("workflow_version", "")),
            plan_version=str(value.get("plan_version", "")),
            active_stage_id=str(value.get("active_stage_id", "")),
            active_task_id=str(value.get("active_task_id", "")),
            status=CheckpointStatus(
                str(value.get("status", CheckpointStatus.CREATED.value))
            ),
            execution_plan=value.get("execution_plan", {}) or {},
            target_summary=str(value.get("target_summary", "")),
            completed_stage_ids=tuple(value.get("completed_stage_ids", []) or ()),
            completed_task_ids=tuple(value.get("completed_task_ids", []) or ()),
            artifacts=tuple(
                ArtifactSnapshot.from_dict(item)
                for item in (value.get("artifacts", []) or ())
            ),
            verifier_status=str(value.get("verifier_status", "UNKNOWN")),
            failure_event=(
                FailureEventSnapshot.from_dict(value["failure_event"])
                if value.get("failure_event") else None
            ),
            proposed_next_action=value.get("proposed_next_action"),
            task_effect_records=tuple(
                TaskEffectRecord.from_dict(item)
                for item in (value.get("task_effect_records", []) or ())
            ),
            idempotency_keys=tuple(value.get("idempotency_keys", []) or ()),
            external_state_guards=tuple(
                ExternalStateGuard.from_dict(item)
                for item in (value.get("external_state_guards", []) or ())
            ),
            invalidation_reasons=tuple(value.get("invalidation_reasons", []) or ()),
            runtime_evidence=tuple(
                RuntimeEvidence.from_dict(item)
                for item in (value.get("runtime_evidence", []) or ())
            ),
            checkpoint_schema_version=str(value.get("checkpoint_schema_version", "1.0")),
            contract_version=str(value.get("contract_version", "v2.2A")),
            created_at=str(value.get("created_at", "")),
            updated_at=str(value.get("updated_at", "")),
            supersedes_run_id=value.get("supersedes_run_id"),
        )


@dataclass(frozen=True)
class ResumeDecision:
    """Outcome of deterministic validation, separate from the chosen action."""

    disposition: ResumeDisposition
    action: Optional[ResumeAction]
    run_id: str
    checkpoint_id: str
    resume_stage_id: Optional[str]
    resume_task_id: Optional[str]
    resulting_status: CheckpointStatus
    reason_code: ResumeReasonCode
    evidence: tuple[RuntimeEvidence, ...] = ()
    clarification_question: Optional[str] = None

    def __post_init__(self) -> None:
        if not str(self.run_id).strip() or not str(self.checkpoint_id).strip():
            raise ValueError("ResumeDecision 必须包含 run_id 与 checkpoint_id")
        object.__setattr__(self, "disposition", ResumeDisposition(self.disposition))
        object.__setattr__(self, "resulting_status", CheckpointStatus(self.resulting_status))
        object.__setattr__(self, "reason_code", ResumeReasonCode(self.reason_code))
        if self.action is not None:
            object.__setattr__(self, "action", ResumeAction(self.action))
        object.__setattr__(
            self,
            "evidence",
            tuple(
                item if isinstance(item, RuntimeEvidence)
                else RuntimeEvidence.from_dict(cast(Mapping[str, Any], item))
                for item in (self.evidence or ())
            ),
        )
        if self.disposition is ResumeDisposition.ALLOW and self.action is None:
            raise ValueError("ALLOW 必须包含 ResumeAction")
        if self.disposition is not ResumeDisposition.ALLOW and self.action is not None:
            raise ValueError("REQUIRE_CLARIFICATION/REJECT 不得包含 ResumeAction")
        if (
            self.disposition is ResumeDisposition.REQUIRE_CLARIFICATION
            and self.resulting_status is not CheckpointStatus.WAITING_USER
        ):
            raise ValueError("REQUIRE_CLARIFICATION 的 resulting_status 必须为 WAITING_USER")
        if self.disposition is ResumeDisposition.REQUIRE_CLARIFICATION and not self.clarification_question:
            raise ValueError("REQUIRE_CLARIFICATION 必须提供 clarification_question")

    def to_dict(self) -> dict[str, Any]:
        return {
            "disposition": self.disposition.value,
            "action": self.action.value if self.action else None,
            "run_id": self.run_id,
            "checkpoint_id": self.checkpoint_id,
            "resume_stage_id": self.resume_stage_id,
            "resume_task_id": self.resume_task_id,
            "resulting_status": self.resulting_status.value,
            "reason_code": self.reason_code.value,
            "evidence": [item.to_dict() for item in self.evidence],
            "clarification_question": self.clarification_question,
        }


__all__ = [
    "ArtifactSnapshot",
    "ExternalStateGuard",
    "FailureEventSnapshot",
    "ResumeContext",
    "ResumeDecision",
    "RunCheckpoint",
    "RuntimeEvidence",
    "TaskEffectRecord",
]
