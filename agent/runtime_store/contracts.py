"""Immutable return contracts for v2.3B SQLite primitives."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Mapping

from agent.checkpoint.contracts import RunCheckpoint

if TYPE_CHECKING:
    from agent.run_resume.contracts import RunResumeIndex


@dataclass(frozen=True)
class RunHead:
    """The current mutable head for one logical Run."""

    tenant_id: str
    session_id: str
    run_id: str
    request_id: str
    current_revision: int
    current_digest: str
    current_writer_id: str
    current_fence_token: int
    store_generation: str
    run_status: str
    updated_at: str


@dataclass(frozen=True)
class FenceGrant:
    """The writer token currently granted for one Run."""

    tenant_id: str
    session_id: str
    run_id: str
    writer_id: str
    fence_token: int
    fence_epoch: int
    run_revision: int
    store_generation: str
    idempotent: bool = False


@dataclass(frozen=True)
class RevisionRecord:
    """One immutable Run revision appended behind a RunHead CAS."""

    tenant_id: str
    session_id: str
    run_id: str
    revision: int
    parent_digest: str
    payload_json: str
    payload_digest: str
    request_id: str
    writer_id: str
    fence_token: int
    created_at: str


@dataclass(frozen=True)
class PreparedOperation:
    """A durable pre-side-effect intent reserved by ``prepare_operation``."""

    tenant_id: str
    session_id: str
    run_id: str
    operation_id: str
    idempotency_key: str
    operation_type: str
    request_digest: str
    expected_effect_digest: str
    effect_state: str
    external_reference: str
    result_json: str
    result_digest: str
    prepared_revision: int
    committed_revision: int | None
    request_id: str
    fence_epoch: int
    run_revision: int
    store_generation: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ServiceStartReservation:
    """Durable reservation for one public ``start_run`` request."""

    head: RunHead
    intent: PreparedOperation
    created: bool


@dataclass(frozen=True)
class RunOutputRecord:
    """Durable, user-visible final output for one logical Run revision."""

    tenant_id: str
    session_id: str
    run_id: str
    revision: int
    text: str
    evidence_ids: tuple[str, ...]
    artifact_ids: tuple[str, ...]
    created_at: str


@dataclass(frozen=True)
class RunReadSnapshot:
    """One consistent read view used by the public Service projector."""

    head: RunHead
    index: Any | None
    start_intent: PreparedOperation | None
    terminal_event: DurableEventRecord | None = None
    output: RunOutputRecord | None = None


@dataclass(frozen=True)
class DurableEventRecord:
    """Immutable JSON record returned by the durable event table."""

    event_id: str
    sequence_number: int
    tenant_id: str
    session_id: str
    run_id: str
    workflow_id: str | None
    stage_id: str | None
    task_id: str | None
    event_type: str
    timestamp: str
    payload_json: str
    payload_digest: str
    run_revision: int


@dataclass(frozen=True)
class DurableEventHead:
    """Cursor metadata for one Run's durable event stream."""

    tenant_id: str
    session_id: str
    run_id: str
    latest_sequence: int
    retained_from_sequence: int
    terminal_sequence: int | None


@dataclass(frozen=True)
class ArtifactCommitFact:
    """Verified, JSON-only artifact fact committed beside a checkpoint."""

    artifact_id: str
    artifact_type: str
    reference: str
    digest: str
    producer_workflow_id: str
    producer_stage_id: str
    exists: bool = True
    verified: bool = True
    verification_evidence_digest: str = ""
    producer_task_id: str = ""

    def __post_init__(self) -> None:
        for name in (
            "artifact_id",
            "artifact_type",
            "reference",
            "digest",
            "producer_workflow_id",
            "producer_stage_id",
        ):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"ArtifactCommitFact.{name} must be non-empty")

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "artifact_type": self.artifact_type,
            "reference": self.reference,
            "digest": self.digest,
            "producer_workflow_id": self.producer_workflow_id,
            "producer_stage_id": self.producer_stage_id,
            "exists": self.exists,
            "verified": self.verified,
            "verification_evidence_digest": self.verification_evidence_digest,
            "producer_task_id": self.producer_task_id,
        }


class FinalizationFailurePoint(str, Enum):
    """Deterministic test-only rollback points for the bundle transaction."""

    AFTER_CHECKPOINT_INSERT = "AFTER_CHECKPOINT_INSERT"
    AFTER_ARTIFACT_METADATA = "AFTER_ARTIFACT_METADATA"
    AFTER_LEDGER_UPDATE = "AFTER_LEDGER_UPDATE"
    AFTER_INDEX_INSERT = "AFTER_INDEX_INSERT"
    BEFORE_COMMIT = "BEFORE_COMMIT"


@dataclass(frozen=True)
class FinalizationBundle:
    """All externally verified facts required for one atomic finalization."""

    tenant_id: str
    session_id: str
    run_id: str
    workflow_id: str
    request_id: str
    writer_id: str
    fence_epoch: int
    expected_revision: int
    expected_parent_digest: str
    idempotency_key: str
    operation_type: str
    request_digest: str
    checkpoint: RunCheckpoint
    artifacts: tuple[ArtifactCommitFact, ...]
    next_run_index: RunResumeIndex
    external_result_digest: str
    verifier_status: str
    checkpoint_chain: tuple[RunCheckpoint, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "artifacts", tuple(self.artifacts or ()))
        chain = tuple(self.checkpoint_chain or ())
        if not chain:
            chain = (self.checkpoint,)
        # ``dataclasses.replace(bundle, checkpoint=...)`` is used by callers
        # to mutate the single-checkpoint legacy form.  Treat that form as an
        # implicit chain; an explicitly supplied multi-checkpoint chain must
        # still end in the declared final checkpoint.
        if len(chain) == 1 and chain[0] != self.checkpoint:
            chain = (self.checkpoint,)
        if chain[-1] != self.checkpoint:
            raise ValueError(
                "FinalizationBundle.checkpoint 必须是 checkpoint_chain 的最后一项"
            )
        object.__setattr__(self, "checkpoint_chain", chain)
        if self.fence_epoch <= 0:
            raise ValueError("FinalizationBundle.fence_epoch must be > 0")
        if self.expected_revision < 0:
            raise ValueError("FinalizationBundle.expected_revision must be >= 0")


@dataclass(frozen=True)
class FinalizationResult:
    """Stable result returned for the first commit and every idempotent retry."""

    tenant_id: str
    session_id: str
    run_id: str
    workflow_id: str
    idempotency_key: str
    operation_id: str
    effect_state: str
    result_digest: str
    checkpoint_id: str
    checkpoint_digest: str
    run_revision: int
    run_index_digest: str
    artifact_ids: tuple[str, ...]
    committed_at: str
    store_generation: str
    idempotent: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "workflow_id": self.workflow_id,
            "idempotency_key": self.idempotency_key,
            "operation_id": self.operation_id,
            "effect_state": self.effect_state,
            "result_digest": self.result_digest,
            "checkpoint_id": self.checkpoint_id,
            "checkpoint_digest": self.checkpoint_digest,
            "run_revision": self.run_revision,
            "run_index_digest": self.run_index_digest,
            "artifact_ids": list(self.artifact_ids),
            "committed_at": self.committed_at,
            "store_generation": self.store_generation,
            "idempotent": self.idempotent,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], *, idempotent: bool = True) -> "FinalizationResult":
        return cls(
            tenant_id=str(value.get("tenant_id", "")),
            session_id=str(value.get("session_id", "")),
            run_id=str(value.get("run_id", "")),
            workflow_id=str(value.get("workflow_id", "")),
            idempotency_key=str(value.get("idempotency_key", "")),
            operation_id=str(value.get("operation_id", "")),
            effect_state=str(value.get("effect_state", "COMMITTED")),
            result_digest=str(value.get("result_digest", "")),
            checkpoint_id=str(value.get("checkpoint_id", "")),
            checkpoint_digest=str(value.get("checkpoint_digest", "")),
            run_revision=int(value.get("run_revision", 0)),
            run_index_digest=str(value.get("run_index_digest", "")),
            artifact_ids=tuple(str(item) for item in value.get("artifact_ids", []) or ()),
            committed_at=str(value.get("committed_at", "")),
            store_generation=str(value.get("store_generation", "")),
            idempotent=idempotent,
        )


__all__ = [
    "FenceGrant",
    "ArtifactCommitFact",
    "FinalizationBundle",
    "FinalizationFailurePoint",
    "FinalizationResult",
    "PreparedOperation",
    "RevisionRecord",
    "RunReadSnapshot",
    "RunOutputRecord",
    "DurableEventRecord",
    "DurableEventHead",
    "ServiceStartReservation",
    "RunHead",
]
