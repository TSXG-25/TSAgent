"""Immutable production contracts for v2.2C Run-level resume.

This module stores only Run-level coordination facts.  Stage/Task truth stays
in ``agent.checkpoint.RunCheckpoint`` and is validated by the v2.2B runtime.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Mapping, Optional, cast

from agent.checkpoint import ExternalStateGuard, ResumeAction


class RunWorkflowStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUSPENDED = "SUSPENDED"
    WAITING_USER = "WAITING_USER"
    FAILED_RECOVERABLE = "FAILED_RECOVERABLE"
    FAILED_TERMINAL = "FAILED_TERMINAL"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class RunResumeDisposition(str, Enum):
    ALLOW = "ALLOW"
    REQUIRE_CLARIFICATION = "REQUIRE_CLARIFICATION"
    REJECT = "REJECT"


class RunResumeReasonCode(str, Enum):
    ALLOWED_ACTIVE_WORKFLOW = "ALLOWED_ACTIVE_WORKFLOW"
    ALLOWED_REPLAY_ACTIVE_WORKFLOW = "ALLOWED_REPLAY_ACTIVE_WORKFLOW"
    AMBIGUOUS_RUN = "AMBIGUOUS_RUN"
    RUN_MISMATCH = "RUN_MISMATCH"
    RUN_INDEX_INCONSISTENT = "RUN_INDEX_INCONSISTENT"
    UPSTREAM_WORKFLOW_INCOMPLETE = "UPSTREAM_WORKFLOW_INCOMPLETE"
    UPSTREAM_ARTIFACT_MISSING = "UPSTREAM_ARTIFACT_MISSING"
    UPSTREAM_ARTIFACT_CHANGED = "UPSTREAM_ARTIFACT_CHANGED"
    WORKFLOW_VERSION_INCOMPATIBLE = "WORKFLOW_VERSION_INCOMPATIBLE"
    ACTIVE_CHECKPOINT_MISMATCH = "ACTIVE_CHECKPOINT_MISMATCH"
    CHECKPOINT_NOT_FOUND = "CHECKPOINT_NOT_FOUND"
    UNKNOWN_SIDE_EFFECT = "UNKNOWN_SIDE_EFFECT"
    DUPLICATE_SIDE_EFFECT = "DUPLICATE_SIDE_EFFECT"
    NON_IDEMPOTENT_STAGE = "NON_IDEMPOTENT_STAGE"
    NO_ACTIVE_WORKFLOW = "NO_ACTIVE_WORKFLOW"
    RUN_COMPLETED = "RUN_COMPLETED"


def _unique(values: tuple[str, ...], label: str) -> tuple[str, ...]:
    normalized = tuple(str(value) for value in values if str(value).strip())
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{label} 不得重复")
    return normalized


@dataclass(frozen=True)
class ArtifactRequirement:
    artifact_id: str
    expected_digest: str

    def __post_init__(self) -> None:
        if not self.artifact_id.strip() or not self.expected_digest.strip():
            raise ValueError("ArtifactRequirement 必须包含 artifact_id 和 expected_digest")

    def to_dict(self) -> dict[str, str]:
        return {
            "artifact_id": self.artifact_id,
            "expected_digest": self.expected_digest,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ArtifactRequirement":
        return cls(
            artifact_id=str(value.get("artifact_id", "")),
            expected_digest=str(value.get("expected_digest", "")),
        )


@dataclass(frozen=True)
class RunArtifactFact:
    artifact_id: str
    producer_workflow_id: str
    digest: str
    exists: bool = True
    verified: bool = True
    artifact_type: str = ""
    reference: str = ""
    encoding: str = "utf-8"
    producer_stage_id: str = ""
    producer_task_id: str = ""

    def __post_init__(self) -> None:
        if not self.artifact_id.strip() or not self.producer_workflow_id.strip():
            raise ValueError("RunArtifactFact 必须包含 artifact_id 和 producer_workflow_id")

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "producer_workflow_id": self.producer_workflow_id,
            "digest": self.digest,
            "exists": self.exists,
            "verified": self.verified,
            "artifact_type": self.artifact_type,
            "reference": self.reference,
            "encoding": self.encoding,
            "producer_stage_id": self.producer_stage_id,
            "producer_task_id": self.producer_task_id,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RunArtifactFact":
        return cls(
            artifact_id=str(value.get("artifact_id", "")),
            producer_workflow_id=str(value.get("producer_workflow_id", "")),
            digest=str(value.get("digest", "")),
            exists=bool(value.get("exists", True)),
            verified=bool(value.get("verified", True)),
            artifact_type=str(value.get("artifact_type", "")),
            reference=str(value.get("reference", "")),
            encoding=str(value.get("encoding", "utf-8")),
            producer_stage_id=str(value.get("producer_stage_id", "")),
            producer_task_id=str(value.get("producer_task_id", "")),
        )


@dataclass(frozen=True)
class WorkflowDependency:
    workflow_id: str
    depends_on: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "depends_on", _unique(self.depends_on, "depends_on"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "depends_on": list(self.depends_on),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "WorkflowDependency":
        return cls(
            workflow_id=str(value.get("workflow_id", "")),
            depends_on=tuple(str(item) for item in value.get("depends_on", []) or ()),
        )


@dataclass(frozen=True)
class WorkflowSummary:
    """Run-level Workflow projection; no Stage/Task state is copied here."""

    workflow_id: str
    workflow_version: str
    status: RunWorkflowStatus
    checkpoint_id: str = ""
    depends_on: tuple[str, ...] = ()
    required_artifacts: tuple[ArtifactRequirement, ...] = ()
    active_side_effect_state: str = "NONE"
    active_stage_idempotent: bool = False
    verifier_status: str = "UNKNOWN"
    activation_attempt_id: str = ""

    def __post_init__(self) -> None:
        if not self.workflow_id.strip() or not self.workflow_version.strip():
            raise ValueError("WorkflowSummary 必须包含 workflow_id 和 workflow_version")
        object.__setattr__(self, "status", RunWorkflowStatus(self.status))
        object.__setattr__(self, "depends_on", _unique(self.depends_on, "depends_on"))
        object.__setattr__(
            self,
            "required_artifacts",
            tuple(
                item if isinstance(item, ArtifactRequirement)
                else ArtifactRequirement.from_dict(cast(Mapping[str, Any], item))
                for item in (self.required_artifacts or ())
            ),
        )
        if (
            self.status is not RunWorkflowStatus.PENDING
            and not self.checkpoint_id.strip()
            and not (
                self.status is RunWorkflowStatus.RUNNING
                and self.activation_attempt_id.strip()
            )
        ):
            raise ValueError("已开始或已完成的 Workflow 必须包含 checkpoint_id")

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "workflow_version": self.workflow_version,
            "status": self.status.value,
            "checkpoint_id": self.checkpoint_id,
            "depends_on": list(self.depends_on),
            "required_artifacts": [item.to_dict() for item in self.required_artifacts],
            "active_side_effect_state": self.active_side_effect_state,
            "active_stage_idempotent": self.active_stage_idempotent,
            "verifier_status": self.verifier_status,
            "activation_attempt_id": self.activation_attempt_id,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "WorkflowSummary":
        return cls(
            workflow_id=str(value.get("workflow_id", "")),
            workflow_version=str(value.get("workflow_version", "")),
            status=RunWorkflowStatus(str(value.get("status", RunWorkflowStatus.PENDING.value))),
            checkpoint_id=str(value.get("checkpoint_id", "")),
            depends_on=tuple(str(item) for item in value.get("depends_on", []) or ()),
            required_artifacts=tuple(
                ArtifactRequirement.from_dict(item)
                for item in value.get("required_artifacts", []) or ()
            ),
            active_side_effect_state=str(value.get("active_side_effect_state", "NONE")),
            active_stage_idempotent=bool(value.get("active_stage_idempotent", False)),
            verifier_status=str(value.get("verifier_status", "UNKNOWN")),
            activation_attempt_id=str(value.get("activation_attempt_id", "")),
        )


@dataclass(frozen=True)
class RunResumeIndex:
    """Immutable latest Run-level coordination snapshot."""

    run_id: str
    workflow_sequence: tuple[str, ...]
    workflows: tuple[WorkflowSummary, ...]
    completed_workflow_ids: tuple[str, ...]
    active_workflow_id: str
    active_checkpoint_id: str
    pending_workflow_ids: tuple[str, ...]
    workflow_dependencies: tuple[WorkflowDependency, ...]
    artifacts: tuple[RunArtifactFact, ...] = ()
    store_generation: str = "store-1"
    index_version: str = "v2.2C"
    revision: int = 0
    parent_digest: str = ""
    created_at: str = ""
    updated_at: str = ""
    session_id: str = "default-session"
    conversation_id: str = "default-conversation"
    user_scope: str = "default-user"

    def __post_init__(self) -> None:
        if not self.run_id.strip():
            raise ValueError("RunResumeIndex.run_id 不能为空")
        if self.revision < 0:
            raise ValueError("RunResumeIndex.revision 不能为负数")
        for name in ("session_id", "conversation_id", "user_scope"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"RunResumeIndex.{name} 不能为空")
        sequence = _unique(self.workflow_sequence, "workflow_sequence")
        object.__setattr__(self, "workflow_sequence", sequence)
        workflows = tuple(
            item if isinstance(item, WorkflowSummary)
            else WorkflowSummary.from_dict(cast(Mapping[str, Any], item))
            for item in (self.workflows or ())
        )
        object.__setattr__(self, "workflows", workflows)
        ids = tuple(item.workflow_id for item in workflows)
        if set(ids) != set(sequence) or len(ids) != len(sequence):
            raise ValueError("workflows 必须与 workflow_sequence 一一对应")

        completed = _unique(self.completed_workflow_ids, "completed_workflow_ids")
        pending = _unique(self.pending_workflow_ids, "pending_workflow_ids")
        object.__setattr__(self, "completed_workflow_ids", completed)
        object.__setattr__(self, "pending_workflow_ids", pending)
        known = set(sequence)
        if not set(completed).issubset(known) or not set(pending).issubset(known):
            raise ValueError("completed/pending Workflow 必须属于 workflow_sequence")
        if set(completed).intersection(pending):
            raise ValueError("completed_workflow_ids 与 pending_workflow_ids 不得重叠")
        if self.active_workflow_id and self.active_workflow_id not in known:
            raise ValueError("active_workflow_id 必须属于 workflow_sequence")
        if self.active_workflow_id in completed or self.active_workflow_id in pending:
            raise ValueError("active Workflow 不得同时属于 completed/pending")
        partition = set(completed) | set(pending)
        if self.active_workflow_id:
            partition.add(self.active_workflow_id)
        if partition != known:
            raise ValueError("completed/active/pending 必须覆盖全部 Workflow")

        by_id = {item.workflow_id: item for item in workflows}
        if self.active_workflow_id:
            active = by_id[self.active_workflow_id]
            activation_pending = (
                active.status is RunWorkflowStatus.RUNNING
                and bool(active.activation_attempt_id)
                and not active.checkpoint_id
            )
            if not activation_pending and active.checkpoint_id != self.active_checkpoint_id:
                raise ValueError("active_checkpoint_id 必须指向 active Workflow 的 checkpoint")
            if activation_pending and self.active_checkpoint_id:
                raise ValueError("启动事务尚未生成 checkpoint 时 active_checkpoint_id 必须为空")
        elif self.active_checkpoint_id:
            raise ValueError("没有 active Workflow 时不得存在 active_checkpoint_id")

        dependencies = tuple(
            item if isinstance(item, WorkflowDependency)
            else WorkflowDependency.from_dict(cast(Mapping[str, Any], item))
            for item in (self.workflow_dependencies or ())
        )
        object.__setattr__(self, "workflow_dependencies", dependencies)
        dependency_map = {item.workflow_id: item.depends_on for item in dependencies}
        positions = {workflow_id: index for index, workflow_id in enumerate(sequence)}
        for workflow_id in sequence:
            declared = by_id[workflow_id].depends_on
            mapped = dependency_map.get(workflow_id, ())
            if tuple(declared) != tuple(mapped):
                raise ValueError("workflow_dependencies 必须与 WorkflowSummary.depends_on 一致")
            for upstream in mapped:
                if upstream not in known or upstream == workflow_id:
                    raise ValueError("Workflow dependency 必须引用其他已知 Workflow")
                if positions[upstream] >= positions[workflow_id]:
                    raise ValueError("v2.2C 仅支持按 Run 顺序的 Workflow dependency")

        artifacts = tuple(
            item if isinstance(item, RunArtifactFact)
            else RunArtifactFact.from_dict(cast(Mapping[str, Any], item))
            for item in (self.artifacts or ())
        )
        object.__setattr__(self, "artifacts", artifacts)
        if len({item.artifact_id for item in artifacts}) != len(artifacts):
            raise ValueError("RunResumeIndex.artifacts 不得重复")
        if any(item.producer_workflow_id not in known for item in artifacts):
            raise ValueError("Artifact producer 必须属于 workflow_sequence")

    def workflow(self, workflow_id: str) -> WorkflowSummary | None:
        return next(
            (item for item in self.workflows if item.workflow_id == workflow_id),
            None,
        )

    def evolve(self, *, parent_digest: str = "", **changes: Any) -> "RunResumeIndex":
        """Create a new immutable index revision."""
        return replace(
            self,
            revision=self.revision + 1,
            parent_digest=parent_digest,
            **changes,
        )

    def with_active_checkpoint(
        self,
        checkpoint_id: str,
        *,
        status: RunWorkflowStatus,
        verifier_status: str = "UNKNOWN",
        updated_at: str = "",
        parent_digest: str = "",
    ) -> "RunResumeIndex":
        if not self.active_workflow_id:
            raise ValueError("没有 active Workflow")
        active = self.workflow(self.active_workflow_id)
        if active is None:
            raise ValueError("active Workflow 不存在")
        updated = replace(
            active,
            status=status,
            checkpoint_id=checkpoint_id,
            verifier_status=verifier_status,
        )
        workflows = tuple(
            updated if item.workflow_id == active.workflow_id else item
            for item in self.workflows
        )
        return self.evolve(
            parent_digest=parent_digest,
            workflows=workflows,
            active_checkpoint_id=checkpoint_id,
            updated_at=updated_at,
        )

    def complete_active(
        self,
        checkpoint_id: str,
        *,
        updated_at: str = "",
        parent_digest: str = "",
        artifacts: tuple[RunArtifactFact, ...] = (),
    ) -> "RunResumeIndex":
        if not self.active_workflow_id:
            raise ValueError("没有 active Workflow")
        active = self.workflow(self.active_workflow_id)
        if active is None or not checkpoint_id.strip():
            raise ValueError("完成的 checkpoint 必须属于 active Workflow 且不能为空")
        artifact_map = {item.artifact_id: item for item in self.artifacts}
        for artifact in artifacts:
            if artifact.producer_workflow_id != active.workflow_id:
                raise ValueError("完成 Workflow 发布的 Artifact producer 必须是 active Workflow")
            artifact_map[artifact.artifact_id] = artifact
        merged_artifacts = tuple(
            artifact_map[item.artifact_id]
            for item in self.artifacts
        ) + tuple(
            artifact
            for artifact_id, artifact in artifact_map.items()
            if artifact_id not in {item.artifact_id for item in self.artifacts}
        )
        completed = tuple(dict.fromkeys((*self.completed_workflow_ids, active.workflow_id)))
        workflows = tuple(
            replace(
                item,
                status=RunWorkflowStatus.COMPLETED,
                checkpoint_id=checkpoint_id,
                verifier_status="VERIFIED",
            )
            if item.workflow_id == active.workflow_id else item
            for item in self.workflows
        )
        return self.evolve(
            parent_digest=parent_digest,
            workflows=workflows,
            completed_workflow_ids=completed,
            active_workflow_id="",
            active_checkpoint_id="",
            updated_at=updated_at,
            artifacts=merged_artifacts,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "workflow_sequence": list(self.workflow_sequence),
            "workflows": [item.to_dict() for item in self.workflows],
            "completed_workflow_ids": list(self.completed_workflow_ids),
            "active_workflow_id": self.active_workflow_id,
            "active_checkpoint_id": self.active_checkpoint_id,
            "pending_workflow_ids": list(self.pending_workflow_ids),
            "workflow_dependencies": [item.to_dict() for item in self.workflow_dependencies],
            "artifacts": [item.to_dict() for item in self.artifacts],
            "store_generation": self.store_generation,
            "index_version": self.index_version,
            "revision": self.revision,
            "parent_digest": self.parent_digest,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "session_id": self.session_id,
            "conversation_id": self.conversation_id,
            "user_scope": self.user_scope,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RunResumeIndex":
        return cls(
            run_id=str(value.get("run_id", "")),
            workflow_sequence=tuple(str(item) for item in value.get("workflow_sequence", []) or ()),
            workflows=tuple(
                WorkflowSummary.from_dict(item)
                for item in value.get("workflows", []) or ()
            ),
            completed_workflow_ids=tuple(
                str(item) for item in value.get("completed_workflow_ids", []) or ()
            ),
            active_workflow_id=str(value.get("active_workflow_id", "")),
            active_checkpoint_id=str(value.get("active_checkpoint_id", "")),
            pending_workflow_ids=tuple(
                str(item) for item in value.get("pending_workflow_ids", []) or ()
            ),
            workflow_dependencies=tuple(
                WorkflowDependency.from_dict(item)
                for item in value.get("workflow_dependencies", []) or ()
            ),
            artifacts=tuple(
                RunArtifactFact.from_dict(item)
                for item in value.get("artifacts", []) or ()
            ),
            store_generation=str(value.get("store_generation", "store-1")),
            index_version=str(value.get("index_version", "v2.2C")),
            revision=int(value.get("revision", 0)),
            parent_digest=str(value.get("parent_digest", "")),
            created_at=str(value.get("created_at", "")),
            updated_at=str(value.get("updated_at", "")),
            session_id=str(value.get("session_id", "default-session")),
            conversation_id=str(value.get("conversation_id", "default-conversation")),
            user_scope=str(value.get("user_scope", "default-user")),
        )


@dataclass(frozen=True)
class RunResumeRequest:
    requested_run_id: str = ""
    candidate_run_ids: tuple[str, ...] = ()
    requested_workflow_id: str = ""
    requested_checkpoint_id: str = ""
    requested_action: ResumeAction = ResumeAction.RESUME_EXACT
    current_workflow_versions: tuple[tuple[str, str], ...] = ()
    observed_artifacts: tuple[RunArtifactFact, ...] = ()
    external_state_evidence: tuple[ExternalStateGuard, ...] = ()
    rehydrated_from_store: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "requested_action", ResumeAction(self.requested_action))
        object.__setattr__(self, "candidate_run_ids", _unique(self.candidate_run_ids, "candidate_run_ids"))
        object.__setattr__(
            self,
            "current_workflow_versions",
            tuple((str(key), str(value)) for key, value in self.current_workflow_versions),
        )
        object.__setattr__(
            self,
            "observed_artifacts",
            tuple(
                item if isinstance(item, RunArtifactFact)
                else RunArtifactFact.from_dict(cast(Mapping[str, Any], item))
                for item in (self.observed_artifacts or ())
            ),
        )
        object.__setattr__(
            self,
            "external_state_evidence",
            tuple(
                item if isinstance(item, ExternalStateGuard)
                else ExternalStateGuard.from_dict(cast(Mapping[str, Any], item))
                for item in (self.external_state_evidence or ())
            ),
        )

    def version_for(self, workflow_id: str) -> str:
        return dict(self.current_workflow_versions).get(workflow_id, "")

    def artifact(self, artifact_id: str) -> RunArtifactFact | None:
        return next(
            (item for item in self.observed_artifacts if item.artifact_id == artifact_id),
            None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested_run_id": self.requested_run_id,
            "candidate_run_ids": list(self.candidate_run_ids),
            "requested_workflow_id": self.requested_workflow_id,
            "requested_checkpoint_id": self.requested_checkpoint_id,
            "requested_action": self.requested_action.value,
            "current_workflow_versions": {
                key: value for key, value in self.current_workflow_versions
            },
            "observed_artifacts": [item.to_dict() for item in self.observed_artifacts],
            "external_state_evidence": [item.to_dict() for item in self.external_state_evidence],
            "rehydrated_from_store": self.rehydrated_from_store,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RunResumeRequest":
        versions = value.get("current_workflow_versions", {}) or {}
        if isinstance(versions, Mapping):
            versions = tuple((str(key), str(item)) for key, item in versions.items())
        return cls(
            requested_run_id=str(value.get("requested_run_id", "")),
            candidate_run_ids=tuple(str(item) for item in value.get("candidate_run_ids", []) or ()),
            requested_workflow_id=str(value.get("requested_workflow_id", "")),
            requested_checkpoint_id=str(value.get("requested_checkpoint_id", "")),
            requested_action=ResumeAction(str(value.get("requested_action", ResumeAction.RESUME_EXACT.value))),
            current_workflow_versions=tuple(versions),
            observed_artifacts=tuple(
                RunArtifactFact.from_dict(item)
                for item in value.get("observed_artifacts", []) or ()
            ),
            external_state_evidence=tuple(
                ExternalStateGuard.from_dict(item)
                for item in value.get("external_state_evidence", []) or ()
            ),
            rehydrated_from_store=bool(value.get("rehydrated_from_store", False)),
        )


__all__ = [
    "ArtifactRequirement",
    "RunArtifactFact",
    "RunResumeDisposition",
    "RunResumeIndex",
    "RunResumeReasonCode",
    "RunResumeRequest",
    "RunWorkflowStatus",
    "WorkflowDependency",
    "WorkflowSummary",
]
