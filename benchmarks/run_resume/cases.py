"""Deterministic v2.2C Run-Level Workflow Resume fixtures.

These fixtures define the coordination contract only.  They do not execute a
Workflow, call an Executor, or claim that the Orchestrator has been upgraded.
The production implementation is intentionally deferred until ADR-0018 and
this oracle are stable.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, cast

from agent.checkpoint import ResumeAction


RUN_RESUME_BENCHMARK_NAME = "run-level-workflow-resume-v2.2c"
RUN_RESUME_BENCHMARK_VERSION = "v0.1"
RUN_RESUME_CONTRACT_VERSION = "adr-0018-v1"


class RunResumeDisposition(str):
    ALLOW = "ALLOW"
    REQUIRE_CLARIFICATION = "REQUIRE_CLARIFICATION"
    REJECT = "REJECT"


class RunResumeReason(str):
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
    UNKNOWN_SIDE_EFFECT = "UNKNOWN_SIDE_EFFECT"
    DUPLICATE_SIDE_EFFECT = "DUPLICATE_SIDE_EFFECT"
    NON_IDEMPOTENT_STAGE = "NON_IDEMPOTENT_STAGE"


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
class ArtifactFact:
    artifact_id: str
    producer_workflow_id: str
    digest: str
    exists: bool = True
    verified: bool = True

    def __post_init__(self) -> None:
        if not self.artifact_id.strip() or not self.producer_workflow_id.strip():
            raise ValueError("ArtifactFact 必须包含 artifact_id 和 producer_workflow_id")

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "producer_workflow_id": self.producer_workflow_id,
            "digest": self.digest,
            "exists": self.exists,
            "verified": self.verified,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ArtifactFact":
        return cls(
            artifact_id=str(value.get("artifact_id", "")),
            producer_workflow_id=str(value.get("producer_workflow_id", "")),
            digest=str(value.get("digest", "")),
            exists=bool(value.get("exists", True)),
            verified=bool(value.get("verified", True)),
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
class WorkflowFact:
    """Run-level summary of one Workflow; no Stage/Task state is duplicated."""

    workflow_id: str
    version: str
    status: str
    checkpoint_id: str
    depends_on: tuple[str, ...] = ()
    required_artifacts: tuple[ArtifactRequirement, ...] = ()
    active_side_effect_state: str = "NONE"
    active_stage_idempotent: bool = False
    verifier_status: str = "UNKNOWN"

    def __post_init__(self) -> None:
        if not self.workflow_id.strip() or not self.version.strip():
            raise ValueError("WorkflowFact 必须包含 workflow_id 和 version")
        if not self.checkpoint_id.strip():
            raise ValueError("WorkflowFact 必须引用 checkpoint_id")
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "version": self.version,
            "status": self.status,
            "checkpoint_id": self.checkpoint_id,
            "depends_on": list(self.depends_on),
            "required_artifacts": [item.to_dict() for item in self.required_artifacts],
            "active_side_effect_state": self.active_side_effect_state,
            "active_stage_idempotent": self.active_stage_idempotent,
            "verifier_status": self.verifier_status,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "WorkflowFact":
        return cls(
            workflow_id=str(value.get("workflow_id", "")),
            version=str(value.get("version", "")),
            status=str(value.get("status", "")),
            checkpoint_id=str(value.get("checkpoint_id", "")),
            depends_on=tuple(str(item) for item in value.get("depends_on", []) or ()),
            required_artifacts=tuple(
                ArtifactRequirement.from_dict(item)
                for item in value.get("required_artifacts", []) or ()
            ),
            active_side_effect_state=str(value.get("active_side_effect_state", "NONE")),
            active_stage_idempotent=bool(value.get("active_stage_idempotent", False)),
            verifier_status=str(value.get("verifier_status", "UNKNOWN")),
        )


@dataclass(frozen=True)
class RunResumeIndex:
    """The minimal Run-level index above Workflow Checkpoint truth."""

    run_id: str
    workflow_sequence: tuple[str, ...]
    workflows: tuple[WorkflowFact, ...]
    completed_workflow_ids: tuple[str, ...]
    active_workflow_id: str
    active_checkpoint_id: str
    pending_workflow_ids: tuple[str, ...]
    workflow_dependencies: tuple[WorkflowDependency, ...]
    artifacts: tuple[ArtifactFact, ...] = ()
    store_generation: str = "store-1"

    def __post_init__(self) -> None:
        if not self.run_id.strip():
            raise ValueError("RunResumeIndex.run_id 不能为空")
        sequence = _unique(self.workflow_sequence, "workflow_sequence")
        object.__setattr__(self, "workflow_sequence", sequence)
        workflows = tuple(
            item if isinstance(item, WorkflowFact)
            else WorkflowFact.from_dict(cast(Mapping[str, Any], item))
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
            if active.checkpoint_id != self.active_checkpoint_id:
                raise ValueError("active_checkpoint_id 必须指向 active Workflow 的最新 checkpoint")
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
                raise ValueError("workflow_dependencies 必须与 WorkflowFact.depends_on 一致")
            for upstream in mapped:
                if upstream not in known or upstream == workflow_id:
                    raise ValueError("Workflow dependency 必须引用其他已知 Workflow")
                if positions[upstream] >= positions[workflow_id]:
                    raise ValueError("v2.2C 仅支持按 Run 顺序的 Workflow dependency")
        artifacts = tuple(
            item if isinstance(item, ArtifactFact)
            else ArtifactFact.from_dict(cast(Mapping[str, Any], item))
            for item in (self.artifacts or ())
        )
        object.__setattr__(self, "artifacts", artifacts)
        if len({item.artifact_id for item in artifacts}) != len(artifacts):
            raise ValueError("RunResumeIndex.artifacts 不得重复")
        if any(item.producer_workflow_id not in known for item in artifacts):
            raise ValueError("Artifact producer 必须属于 workflow_sequence")

    def workflow(self, workflow_id: str) -> WorkflowFact | None:
        return next(
            (item for item in self.workflows if item.workflow_id == workflow_id),
            None,
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
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RunResumeIndex":
        return cls(
            run_id=str(value.get("run_id", "")),
            workflow_sequence=tuple(str(item) for item in value.get("workflow_sequence", []) or ()),
            workflows=tuple(
                WorkflowFact.from_dict(item)
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
                ArtifactFact.from_dict(item)
                for item in value.get("artifacts", []) or ()
            ),
            store_generation=str(value.get("store_generation", "store-1")),
        )


@dataclass(frozen=True)
class RunResumeRequest:
    requested_run_id: str = ""
    candidate_run_ids: tuple[str, ...] = ()
    requested_workflow_id: str = ""
    requested_checkpoint_id: str = ""
    requested_action: str = ResumeAction.RESUME_EXACT.value
    current_workflow_versions: tuple[tuple[str, str], ...] = ()
    observed_artifacts: tuple[ArtifactFact, ...] = ()
    rehydrated_from_store: bool = False

    def __post_init__(self) -> None:
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
                item if isinstance(item, ArtifactFact)
                else ArtifactFact.from_dict(cast(Mapping[str, Any], item))
                for item in (self.observed_artifacts or ())
            ),
        )

    def version_for(self, workflow_id: str) -> str:
        return dict(self.current_workflow_versions).get(workflow_id, "")

    def artifact(self, artifact_id: str) -> ArtifactFact | None:
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
            "requested_action": self.requested_action,
            "current_workflow_versions": {
                key: value for key, value in self.current_workflow_versions
            },
            "observed_artifacts": [item.to_dict() for item in self.observed_artifacts],
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
            requested_action=str(value.get("requested_action", ResumeAction.RESUME_EXACT.value)),
            current_workflow_versions=tuple(versions),
            observed_artifacts=tuple(
                ArtifactFact.from_dict(item)
                for item in value.get("observed_artifacts", []) or ()
            ),
            rehydrated_from_store=bool(value.get("rehydrated_from_store", False)),
        )


@dataclass(frozen=True)
class RunResumeExpected:
    disposition: str
    workflow_action: str | None
    selected_workflow_id: str | None
    selected_checkpoint_id: str | None
    skipped_workflow_ids: tuple[str, ...]
    remaining_workflow_ids: tuple[str, ...]
    reason_code: str
    resulting_status: str
    must_not_execute_workflow_ids: tuple[str, ...] = ()
    post_resume_verifier_status: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "disposition": self.disposition,
            "workflow_action": self.workflow_action,
            "selected_workflow_id": self.selected_workflow_id,
            "selected_checkpoint_id": self.selected_checkpoint_id,
            "skipped_workflow_ids": list(self.skipped_workflow_ids),
            "remaining_workflow_ids": list(self.remaining_workflow_ids),
            "reason_code": self.reason_code,
            "resulting_status": self.resulting_status,
            "must_not_execute_workflow_ids": list(self.must_not_execute_workflow_ids),
            "post_resume_verifier_status": self.post_resume_verifier_status,
        }


@dataclass(frozen=True)
class RunResumeCase:
    id: str
    group: str
    index: RunResumeIndex
    request: RunResumeRequest
    expected: RunResumeExpected
    oracle_only: bool = False
    note: str = ""

    def to_dict(self, *, include_note: bool = True) -> dict[str, Any]:
        value = {
            "id": self.id,
            "group": self.group,
            "index": self.index.to_dict(),
            "request": self.request.to_dict(),
            "expected": self.expected.to_dict(),
            "oracle_only": self.oracle_only,
        }
        if include_note:
            value["note"] = self.note
        return value


def _wf(
    workflow_id: str,
    status: str,
    *,
    version: str = "1.0.0",
    depends_on: tuple[str, ...] = (),
    required_artifacts: tuple[ArtifactRequirement, ...] = (),
    side_effect: str = "NONE",
    idempotent: bool = False,
    verifier: str = "VERIFIED",
) -> WorkflowFact:
    return WorkflowFact(
        workflow_id=workflow_id,
        version=version,
        status=status,
        checkpoint_id=f"cp-{workflow_id}",
        depends_on=depends_on,
        required_artifacts=required_artifacts,
        active_side_effect_state=side_effect,
        active_stage_idempotent=idempotent,
        verifier_status=verifier,
    )


def _index(
    workflows: tuple[WorkflowFact, ...],
    *,
    active: str,
    artifacts: tuple[ArtifactFact, ...] = (),
    store_generation: str = "store-1",
) -> RunResumeIndex:
    completed = tuple(item.workflow_id for item in workflows if item.status == "COMPLETED")
    pending = tuple(item.workflow_id for item in workflows if item.status == "PENDING")
    dependencies = tuple(
        WorkflowDependency(item.workflow_id, item.depends_on)
        for item in workflows
    )
    active_checkpoint = next(
        (item.checkpoint_id for item in workflows if item.workflow_id == active),
        "",
    )
    return RunResumeIndex(
        run_id="run-001",
        workflow_sequence=tuple(item.workflow_id for item in workflows),
        workflows=workflows,
        completed_workflow_ids=completed,
        active_workflow_id=active,
        active_checkpoint_id=active_checkpoint,
        pending_workflow_ids=pending,
        workflow_dependencies=dependencies,
        artifacts=artifacts,
        store_generation=store_generation,
    )


def _request(
    *,
    action: str = ResumeAction.RESUME_EXACT.value,
    requested_run_id: str = "",
    candidate_run_ids: tuple[str, ...] = (),
    requested_checkpoint_id: str = "",
    current_versions: tuple[tuple[str, str], ...] = (),
    artifacts: tuple[ArtifactFact, ...] = (),
    rehydrated: bool = False,
) -> RunResumeRequest:
    return RunResumeRequest(
        requested_run_id=requested_run_id,
        candidate_run_ids=candidate_run_ids,
        requested_checkpoint_id=requested_checkpoint_id,
        requested_action=action,
        current_workflow_versions=current_versions,
        observed_artifacts=artifacts,
        rehydrated_from_store=rehydrated,
    )


def _expected(
    *,
    disposition: str,
    action: str | None,
    selected: str | None,
    checkpoint: str | None,
    skipped: tuple[str, ...],
    remaining: tuple[str, ...],
    reason: str,
    status: str,
    forbidden: tuple[str, ...] = (),
    verifier: str | None = None,
) -> RunResumeExpected:
    return RunResumeExpected(
        disposition=disposition,
        workflow_action=action,
        selected_workflow_id=selected,
        selected_checkpoint_id=checkpoint,
        skipped_workflow_ids=skipped,
        remaining_workflow_ids=remaining,
        reason_code=reason,
        resulting_status=status,
        must_not_execute_workflow_ids=forbidden,
        post_resume_verifier_status=verifier,
    )


def _case(
    case_id: str,
    group: str,
    index: RunResumeIndex,
    request: RunResumeRequest,
    expected: RunResumeExpected,
    *,
    note: str,
    oracle_only: bool = False,
) -> RunResumeCase:
    return RunResumeCase(
        id=case_id,
        group=group,
        index=index,
        request=request,
        expected=expected,
        note=note,
        oracle_only=oracle_only,
    )


def build_cases() -> list[RunResumeCase]:
    """Return the frozen v2.2C seed dataset (16 deterministic cases)."""
    artifact_a = ArtifactFact("artifact.a.result", "wf.a", "sha256:a")
    requirement_a = ArtifactRequirement("artifact.a.result", "sha256:a")
    cases: list[RunResumeCase] = []

    cases.append(_case(
        "run-exact-001", "exact_resume",
        _index((_wf("wf.a", "COMPLETED"), _wf("wf.b", "SUSPENDED", depends_on=("wf.a",))), active="wf.b"),
        _request(),
        _expected(
            disposition=RunResumeDisposition.ALLOW,
            action=ResumeAction.RESUME_EXACT.value,
            selected="wf.b", checkpoint="cp-wf.b", skipped=("wf.a",), remaining=(),
            reason=RunResumeReason.ALLOWED_ACTIVE_WORKFLOW, status="RUNNING",
        ),
        note="A 已完成、B 中断；恢复时只选择 B。",
    ))
    cases.append(_case(
        "run-exact-002", "exact_resume",
        _index((
            _wf("wf.a", "COMPLETED"),
            _wf("wf.b", "COMPLETED", depends_on=("wf.a",)),
            _wf("wf.c", "SUSPENDED", depends_on=("wf.a", "wf.b")),
        ), active="wf.c"),
        _request(),
        _expected(
            disposition=RunResumeDisposition.ALLOW,
            action=ResumeAction.RESUME_EXACT.value,
            selected="wf.c", checkpoint="cp-wf.c", skipped=("wf.a", "wf.b"), remaining=(),
            reason=RunResumeReason.ALLOWED_ACTIVE_WORKFLOW, status="RUNNING",
        ),
        note="A/B 已完成、C 中断；恢复不能回到任何上游 Workflow。",
    ))
    cases.append(_case(
        "run-exact-003", "exact_resume",
        _index((
            _wf("wf.a", "COMPLETED"),
            _wf("wf.b", "SUSPENDED", depends_on=("wf.a",)),
            _wf("wf.c", "PENDING", depends_on=("wf.b",)),
        ), active="wf.b"),
        _request(),
        _expected(
            disposition=RunResumeDisposition.ALLOW,
            action=ResumeAction.RESUME_EXACT.value,
            selected="wf.b", checkpoint="cp-wf.b", skipped=("wf.a",), remaining=("wf.c",),
            reason=RunResumeReason.ALLOWED_ACTIVE_WORKFLOW, status="RUNNING",
        ),
        note="只恢复 active Workflow B，未开始的 C 继续保持 pending。",
    ))

    cases.append(_case(
        "run-replay-001", "replay_active_workflow",
        _index((_wf("wf.a", "COMPLETED"), _wf(
            "wf.b", "SUSPENDED", depends_on=("wf.a",), idempotent=True,
        )), active="wf.b"),
        _request(action=ResumeAction.REPLAY_FROM_STAGE.value),
        _expected(
            disposition=RunResumeDisposition.ALLOW,
            action=ResumeAction.REPLAY_FROM_STAGE.value,
            selected="wf.b", checkpoint="cp-wf.b", skipped=("wf.a",), remaining=(),
            reason=RunResumeReason.ALLOWED_REPLAY_ACTIVE_WORKFLOW, status="RUNNING",
        ),
        note="B 的 active Stage 明确幂等，允许在 B 内 Replay；不影响 A。",
    ))

    cases.append(_case(
        "run-effect-001", "cross_workflow_side_effect",
        _index((
            _wf("wf.a", "COMPLETED", side_effect="COMMITTED"),
            _wf("wf.b", "SUSPENDED", depends_on=("wf.a",)),
        ), active="wf.b"),
        _request(),
        _expected(
            disposition=RunResumeDisposition.ALLOW,
            action=ResumeAction.RESUME_EXACT.value,
            selected="wf.b", checkpoint="cp-wf.b", skipped=("wf.a",), remaining=(),
            reason=RunResumeReason.ALLOWED_ACTIVE_WORKFLOW, status="RUNNING",
            forbidden=("wf.a",),
        ),
        note="A 的已提交副作用属于历史事实，恢复 B 不得重新执行 A。",
    ))
    cases.append(_case(
        "run-effect-002", "cross_workflow_side_effect",
        _index((_wf("wf.a", "COMPLETED"), _wf(
            "wf.b", "WAITING_USER", depends_on=("wf.a",), side_effect="UNKNOWN",
        )), active="wf.b"),
        _request(),
        _expected(
            disposition=RunResumeDisposition.REQUIRE_CLARIFICATION,
            action=None, selected=None, checkpoint=None, skipped=(), remaining=(),
            reason=RunResumeReason.UNKNOWN_SIDE_EFFECT, status="WAITING_USER",
        ),
        note="B 的副作用未知，Run 级协调不能越过 v2.2B 的安全阻断。",
    ))

    cases.append(_case(
        "run-dependency-001", "upstream_dependency",
        _index((_wf("wf.a", "COMPLETED"), _wf(
            "wf.b", "SUSPENDED", depends_on=("wf.a",), required_artifacts=(requirement_a,)
        )), active="wf.b", artifacts=(artifact_a,)),
        _request(artifacts=(artifact_a,)),
        _expected(
            disposition=RunResumeDisposition.ALLOW,
            action=ResumeAction.RESUME_EXACT.value,
            selected="wf.b", checkpoint="cp-wf.b", skipped=("wf.a",), remaining=(),
            reason=RunResumeReason.ALLOWED_ACTIVE_WORKFLOW, status="RUNNING",
        ),
        note="上游 A 的 Artifact 存在、digest 一致且已验证，允许恢复 B。",
    ))
    cases.append(_case(
        "run-dependency-002", "upstream_dependency",
        _index((_wf("wf.a", "COMPLETED"), _wf(
            "wf.b", "SUSPENDED", depends_on=("wf.a",), required_artifacts=(requirement_a,)
        )), active="wf.b", artifacts=(artifact_a,)),
        _request(artifacts=()),
        _expected(
            disposition=RunResumeDisposition.REJECT,
            action=None, selected=None, checkpoint=None, skipped=(), remaining=(),
            reason=RunResumeReason.UPSTREAM_ARTIFACT_MISSING, status="REJECTED",
        ),
        note="上游产物缺失，不允许直接恢复下游 Workflow。",
    ))
    cases.append(_case(
        "run-dependency-003", "upstream_dependency",
        _index((_wf("wf.a", "COMPLETED"), _wf(
            "wf.b", "SUSPENDED", depends_on=("wf.a",), required_artifacts=(requirement_a,)
        )), active="wf.b", artifacts=(artifact_a,)),
        _request(artifacts=(ArtifactFact("artifact.a.result", "wf.a", "sha256:changed"),)),
        _expected(
            disposition=RunResumeDisposition.REJECT,
            action=None, selected=None, checkpoint=None, skipped=(), remaining=(),
            reason=RunResumeReason.UPSTREAM_ARTIFACT_CHANGED, status="REJECTED",
        ),
        note="上游产物 digest 变化，不允许盲目恢复 B。",
    ))

    cases.append(_case(
        "run-selection-001", "run_selection_conflict",
        _index((_wf("wf.a", "COMPLETED"), _wf("wf.b", "SUSPENDED", depends_on=("wf.a",))), active="wf.b"),
        _request(requested_run_id="run-other"),
        _expected(
            disposition=RunResumeDisposition.REJECT,
            action=None, selected=None, checkpoint=None, skipped=(), remaining=(),
            reason=RunResumeReason.RUN_MISMATCH, status="REJECTED",
        ),
        note="显式指定另一个 Run 时，不能把当前 Run 当作恢复目标。",
    ))
    cases.append(_case(
        "run-selection-002", "run_selection_conflict",
        _index((_wf("wf.a", "COMPLETED"), _wf("wf.b", "SUSPENDED", depends_on=("wf.a",))), active="wf.b"),
        _request(candidate_run_ids=("run-001", "run-002")),
        _expected(
            disposition=RunResumeDisposition.REQUIRE_CLARIFICATION,
            action=None, selected=None, checkpoint=None, skipped=(), remaining=(),
            reason=RunResumeReason.AMBIGUOUS_RUN, status="WAITING_USER",
        ),
        note="多个候选 Run 不能依赖最近时间或 LLM 猜测，必须澄清。",
    ))

    cases.append(_case(
        "run-consistency-001", "checkpoint_consistency",
        _index((_wf("wf.a", "COMPLETED"), _wf("wf.b", "SUSPENDED", depends_on=("wf.a",))), active="wf.b"),
        _request(requested_checkpoint_id="cp-wf.b-stale"),
        _expected(
            disposition=RunResumeDisposition.REJECT,
            action=None, selected=None, checkpoint=None, skipped=(), remaining=(),
            reason=RunResumeReason.ACTIVE_CHECKPOINT_MISMATCH, status="REJECTED",
        ),
        note="Run 索引与请求携带的 active checkpoint 不一致，拒绝恢复。",
    ))
    cases.append(_case(
        "run-version-001", "workflow_version",
        _index((_wf("wf.a", "COMPLETED"), _wf("wf.b", "SUSPENDED", depends_on=("wf.a",))), active="wf.b"),
        _request(current_versions=(("wf.b", "2.0.0"),)),
        _expected(
            disposition=RunResumeDisposition.REJECT,
            action=None, selected=None, checkpoint=None, skipped=(), remaining=(),
            reason=RunResumeReason.WORKFLOW_VERSION_INCOMPATIBLE, status="REJECTED",
        ),
        note="当前 Workflow 版本不兼容，v2.2C 不隐式迁移。",
    ))

    cases.append(_case(
        "run-upstream-001", "upstream_dependency",
        _index((_wf("wf.a", "PENDING"), _wf("wf.b", "SUSPENDED", depends_on=("wf.a",))), active="wf.b"),
        _request(),
        _expected(
            disposition=RunResumeDisposition.REJECT,
            action=None, selected=None, checkpoint=None, skipped=(), remaining=("wf.a",),
            reason=RunResumeReason.UPSTREAM_WORKFLOW_INCOMPLETE, status="REJECTED",
        ),
        note="B 的上游 A 尚未完成，不能跨越 Workflow 依赖恢复。",
    ))
    cases.append(_case(
        "run-restart-001", "process_restart",
        _index((_wf("wf.a", "COMPLETED"), _wf("wf.b", "SUSPENDED", depends_on=("wf.a",))), active="wf.b", store_generation="store-2"),
        _request(rehydrated=True),
        _expected(
            disposition=RunResumeDisposition.ALLOW,
            action=ResumeAction.RESUME_EXACT.value,
            selected="wf.b", checkpoint="cp-wf.b", skipped=("wf.a",), remaining=(),
            reason=RunResumeReason.ALLOWED_ACTIVE_WORKFLOW, status="RUNNING",
        ),
        note="从 Store 重建 Run 索引后，决策必须与进程内快照一致。",
    ))
    cases.append(_case(
        "run-completion-001", "resume_completion_evidence",
        _index((_wf("wf.a", "COMPLETED"), _wf("wf.b", "SUSPENDED", depends_on=("wf.a",))), active="wf.b"),
        _request(),
        _expected(
            disposition=RunResumeDisposition.ALLOW,
            action=ResumeAction.RESUME_EXACT.value,
            selected="wf.b", checkpoint="cp-wf.b", skipped=("wf.a",), remaining=(),
            reason=RunResumeReason.ALLOWED_ACTIVE_WORKFLOW, status="RUNNING",
            verifier="VERIFIED",
        ),
        note="恢复闭环完成后的最终产物必须交由 ExecutionVerifier 验证；本 case 只冻结期望证据，不执行 Workflow。",
        oracle_only=True,
    ))
    return cases


__all__ = [
    "ArtifactFact",
    "ArtifactRequirement",
    "RUN_RESUME_BENCHMARK_NAME",
    "RUN_RESUME_BENCHMARK_VERSION",
    "RUN_RESUME_CONTRACT_VERSION",
    "RunResumeCase",
    "RunResumeDisposition",
    "RunResumeExpected",
    "RunResumeIndex",
    "RunResumeReason",
    "RunResumeRequest",
    "WorkflowDependency",
    "WorkflowFact",
    "build_cases",
]
