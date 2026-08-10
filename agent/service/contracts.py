"""Stable, public DTOs for the v2.3C AgentService boundary.

This module intentionally has no dependency on Runtime, Planner, Executor,
Checkpoint, EventBus, or SQLite implementations.  The DTOs are the seam that
CLI, SDK, REST, and desktop adapters may depend on while those internals
continue to evolve.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Protocol, cast

if TYPE_CHECKING:
    from agent.interruption import CancelRunRequest


JSONValue = Any


def _freeze_json(value: Any) -> Any:
    """Freeze a JSON-shaped value and reject process-local live objects."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("public JSON object keys must be strings")
            frozen[key] = _freeze_json(item)
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    raise TypeError(
        "public DTO payloads accept JSON values only; "
        f"got {type(value).__name__}"
    )


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    if isinstance(value, list):
        return [_thaw_json(item) for item in value]
    return value


def _canonical_digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        _thaw_json(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _required_identifier(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _optional_identifier(value: str | None, label: str) -> str | None:
    if value is None:
        return None
    return _required_identifier(value, label)


def _nonnegative_integer(value: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value


def _positive_integer(value: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _freeze_object(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{label} must be a JSON object")
    frozen = _freeze_json(value)
    if not isinstance(frozen, Mapping):  # pragma: no cover - guarded above
        raise TypeError(f"{label} must be a JSON object")
    return cast(Mapping[str, Any], frozen)


def _validate_identity(
    *,
    tenant_id: str,
    user_id: str,
    session_id: str,
    run_id: str,
    request_id: str,
) -> None:
    _required_identifier(tenant_id, "tenant_id")
    _required_identifier(user_id, "user_id")
    _required_identifier(session_id, "session_id")
    _required_identifier(run_id, "run_id")
    _required_identifier(request_id, "request_id")


class RunStatus(str, Enum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    CANCELLING = "CANCELLING"
    SUSPENDED = "SUSPENDED"
    WAITING_USER = "WAITING_USER"
    FAILED_RECOVERABLE = "FAILED_RECOVERABLE"
    FAILED_TERMINAL = "FAILED_TERMINAL"
    BLOCKED = "BLOCKED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"


class ResumeDisposition(str, Enum):
    ALLOW = "ALLOW"
    REQUIRE_CLARIFICATION = "REQUIRE_CLARIFICATION"
    REJECT = "REJECT"


class ResumeAction(str, Enum):
    RESUME_EXACT = "RESUME_EXACT"
    REPLAY_FROM_STAGE = "REPLAY_FROM_STAGE"
    REPLAN_FROM_CHECKPOINT = "REPLAN_FROM_CHECKPOINT"
    ABANDON_AND_RESTART = "ABANDON_AND_RESTART"


class EventType(str, Enum):
    RUN_CREATED = "run_created"
    RUN_STARTED = "run_started"
    RUN_RESUMED = "run_resumed"
    WORKFLOW_ACTIVATED = "workflow_activated"
    WORKFLOW_STARTED = "workflow_started"
    WORKFLOW_COMPLETED = "workflow_completed"
    STAGE_STARTED = "stage_started"
    STAGE_COMPLETED = "stage_completed"
    TASK_STARTED = "task_started"
    TASK_COMPLETED = "task_completed"
    ARTIFACT_PUBLISHED = "artifact_published"
    CHECKPOINT_COMMITTED = "checkpoint_committed"
    RESUME_DECIDED = "resume_decided"
    RUN_CANCELLING = "run_cancelling"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"
    RUN_BLOCKED = "run_blocked"
    RUN_CANCELLED = "run_cancelled"
    RUN_TIMED_OUT = "run_timed_out"

    @property
    def is_terminal(self) -> bool:
        return self in {
            EventType.RUN_COMPLETED,
            EventType.RUN_FAILED,
            EventType.RUN_BLOCKED,
            EventType.RUN_CANCELLED,
            EventType.RUN_TIMED_OUT,
        }


@dataclass(frozen=True)
class StartRunRequest:
    """Request to create or idempotently address one logical Run."""

    tenant_id: str
    user_id: str
    session_id: str
    run_id: str
    request_id: str
    request_text: str
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_identity(
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            session_id=self.session_id,
            run_id=self.run_id,
            request_id=self.request_id,
        )
        if not isinstance(self.request_text, str) or not self.request_text.strip():
            raise ValueError("request_text must be a non-empty string")
        object.__setattr__(self, "metadata", _freeze_object(self.metadata or {}, "metadata"))

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "request_id": self.request_id,
            "request_text": self.request_text,
            "metadata": _thaw_json(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "StartRunRequest":
        return cls(
            tenant_id=str(value.get("tenant_id", "")),
            user_id=str(value.get("user_id", "")),
            session_id=str(value.get("session_id", "")),
            run_id=str(value.get("run_id", "")),
            request_id=str(value.get("request_id", "")),
            request_text=str(value.get("request_text", "")),
            metadata=cast(Mapping[str, JSONValue], value.get("metadata", {}) or {}),
        )

    @property
    def request_digest(self) -> str:
        return _canonical_digest(self.to_dict())


@dataclass(frozen=True)
class ResumeRunRequest:
    """Request to resume an existing logical Run."""

    tenant_id: str
    user_id: str
    session_id: str
    run_id: str
    request_id: str
    request_text: str = ""

    def __post_init__(self) -> None:
        _validate_identity(
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            session_id=self.session_id,
            run_id=self.run_id,
            request_id=self.request_id,
        )
        if not isinstance(self.request_text, str):
            raise ValueError("request_text must be a string")

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "request_id": self.request_id,
            "request_text": self.request_text,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ResumeRunRequest":
        return cls(
            tenant_id=str(value.get("tenant_id", "")),
            user_id=str(value.get("user_id", "")),
            session_id=str(value.get("session_id", "")),
            run_id=str(value.get("run_id", "")),
            request_id=str(value.get("request_id", "")),
            request_text=str(value.get("request_text", "")),
        )

    @property
    def request_digest(self) -> str:
        return _canonical_digest(self.to_dict())


@dataclass(frozen=True)
class RunLookupRequest:
    """Identity-complete request for a Run snapshot or artifact listing."""

    tenant_id: str
    user_id: str
    session_id: str
    run_id: str
    request_id: str

    def __post_init__(self) -> None:
        _validate_identity(
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            session_id=self.session_id,
            run_id=self.run_id,
            request_id=self.request_id,
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "request_id": self.request_id,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RunLookupRequest":
        return cls(
            tenant_id=str(value.get("tenant_id", "")),
            user_id=str(value.get("user_id", "")),
            session_id=str(value.get("session_id", "")),
            run_id=str(value.get("run_id", "")),
            request_id=str(value.get("request_id", "")),
        )


@dataclass(frozen=True)
class EventStreamRequest(RunLookupRequest):
    """Identity-complete cursor for persisted event replay."""

    after_sequence: int = 0
    limit: int | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        _nonnegative_integer(self.after_sequence, "after_sequence")
        if self.limit is not None:
            _positive_integer(self.limit, "limit")

    def to_dict(self) -> dict[str, JSONValue]:
        value: dict[str, JSONValue] = dict(super().to_dict())
        value["after_sequence"] = self.after_sequence
        value["limit"] = self.limit
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EventStreamRequest":
        return cls(
            tenant_id=str(value.get("tenant_id", "")),
            user_id=str(value.get("user_id", "")),
            session_id=str(value.get("session_id", "")),
            run_id=str(value.get("run_id", "")),
            request_id=str(value.get("request_id", "")),
            after_sequence=int(value.get("after_sequence", 0)),
            limit=(
                None
                if value.get("limit") is None
                else int(cast(int, value.get("limit")))
            ),
        )


@dataclass(frozen=True)
class ArtifactSummary:
    """Public artifact metadata; content and internal rows are never exposed."""

    artifact_id: str
    artifact_type: str
    digest: str
    reference: str
    exists: bool
    verified: bool
    producer_workflow_id: str | None = None

    def __post_init__(self) -> None:
        _required_identifier(self.artifact_id, "artifact_id")
        _required_identifier(self.artifact_type, "artifact_type")
        if not isinstance(self.digest, str) or not isinstance(self.reference, str):
            raise ValueError("artifact digest/reference must be strings")
        _optional_identifier(self.producer_workflow_id, "producer_workflow_id")

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "artifact_id": self.artifact_id,
            "artifact_type": self.artifact_type,
            "digest": self.digest,
            "reference": self.reference,
            "exists": self.exists,
            "verified": self.verified,
            "producer_workflow_id": self.producer_workflow_id,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ArtifactSummary":
        return cls(
            artifact_id=str(value.get("artifact_id", "")),
            artifact_type=str(value.get("artifact_type", "")),
            digest=str(value.get("digest", "")),
            reference=str(value.get("reference", "")),
            exists=bool(value.get("exists", False)),
            verified=bool(value.get("verified", False)),
            producer_workflow_id=(
                None
                if value.get("producer_workflow_id") is None
                else str(value.get("producer_workflow_id"))
            ),
        )


ArtifactView = ArtifactSummary


@dataclass(frozen=True)
class ResumeSummary:
    disposition: ResumeDisposition
    action: ResumeAction | None
    reason_code: str
    requires_clarification: bool
    summary: str
    clarification_question: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "disposition", ResumeDisposition(self.disposition))
        if self.action is not None:
            object.__setattr__(self, "action", ResumeAction(self.action))
        _required_identifier(self.reason_code, "reason_code")
        if not isinstance(self.summary, str):
            raise ValueError("summary must be a string")
        if self.disposition is ResumeDisposition.ALLOW and self.action is None:
            raise ValueError("ALLOW requires a ResumeAction")
        if self.disposition is not ResumeDisposition.ALLOW and self.action is not None:
            raise ValueError("non-ALLOW disposition cannot expose a ResumeAction")
        if self.disposition is ResumeDisposition.REQUIRE_CLARIFICATION:
            if not self.requires_clarification:
                raise ValueError("REQUIRE_CLARIFICATION must set requires_clarification")
        elif self.requires_clarification:
            raise ValueError("only REQUIRE_CLARIFICATION may require clarification")

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "disposition": self.disposition.value,
            "action": self.action.value if self.action else None,
            "reason_code": self.reason_code,
            "requires_clarification": self.requires_clarification,
            "summary": self.summary,
            "clarification_question": self.clarification_question,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ResumeSummary":
        action = value.get("action")
        return cls(
            disposition=ResumeDisposition(str(value.get("disposition", ""))),
            action=None if action is None else ResumeAction(str(action)),
            reason_code=str(value.get("reason_code", "")),
            requires_clarification=bool(value.get("requires_clarification", False)),
            summary=str(value.get("summary", "")),
            clarification_question=(
                None
                if value.get("clarification_question") is None
                else str(value.get("clarification_question"))
            ),
        )


@dataclass(frozen=True)
class FailureSummary:
    code: str
    message: str
    retryable: bool
    details: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _required_identifier(self.code, "failure code")
        if not isinstance(self.message, str):
            raise ValueError("failure message must be a string")
        object.__setattr__(self, "details", _freeze_object(self.details or {}, "details"))

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "details": _thaw_json(self.details),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FailureSummary":
        return cls(
            code=str(value.get("code", "")),
            message=str(value.get("message", "")),
            retryable=bool(value.get("retryable", False)),
            details=cast(Mapping[str, JSONValue], value.get("details", {}) or {}),
        )


@dataclass(frozen=True)
class RunOutput:
    """Opaque, durable user-visible output projected from one Run."""

    run_id: str
    revision: int
    text: str
    evidence_ids: tuple[str, ...] = ()
    artifact_ids: tuple[str, ...] = ()
    created_at: str = ""

    def __post_init__(self) -> None:
        _required_identifier(self.run_id, "output run_id")
        _nonnegative_integer(self.revision, "output revision")
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("RunOutput text must be non-empty")
        object.__setattr__(
            self,
            "evidence_ids",
            tuple(_required_identifier(item, "evidence_id") for item in self.evidence_ids),
        )
        object.__setattr__(
            self,
            "artifact_ids",
            tuple(_required_identifier(item, "artifact_id") for item in self.artifact_ids),
        )
        if not isinstance(self.created_at, str) or not self.created_at.strip():
            raise ValueError("RunOutput created_at must be a non-empty string")

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "run_id": self.run_id,
            "revision": self.revision,
            "text": self.text,
            "evidence_ids": list(self.evidence_ids),
            "artifact_ids": list(self.artifact_ids),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RunOutput":
        return cls(
            run_id=str(value.get("run_id", "")),
            revision=int(value.get("revision", 0)),
            text=str(value.get("text", "")),
            evidence_ids=tuple(str(item) for item in value.get("evidence_ids", []) or []),
            artifact_ids=tuple(str(item) for item in value.get("artifact_ids", []) or []),
            created_at=str(value.get("created_at", "")),
        )


@dataclass(frozen=True)
class RunHandle:
    tenant_id: str
    session_id: str
    run_id: str
    request_id: str
    status: RunStatus = RunStatus.CREATED
    revision: int = 0

    def __post_init__(self) -> None:
        _required_identifier(self.tenant_id, "tenant_id")
        _required_identifier(self.session_id, "session_id")
        _required_identifier(self.run_id, "run_id")
        _required_identifier(self.request_id, "request_id")
        object.__setattr__(self, "status", RunStatus(self.status))
        _nonnegative_integer(self.revision, "revision")

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "tenant_id": self.tenant_id,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "request_id": self.request_id,
            "status": self.status.value,
            "revision": self.revision,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RunHandle":
        return cls(
            tenant_id=str(value.get("tenant_id", "")),
            session_id=str(value.get("session_id", "")),
            run_id=str(value.get("run_id", "")),
            request_id=str(value.get("request_id", "")),
            status=RunStatus(str(value.get("status", ""))),
            revision=int(value.get("revision", 0)),
        )


@dataclass(frozen=True)
class RunSnapshot:
    """Stable projection of a Run, deliberately smaller than a checkpoint."""

    tenant_id: str
    run_id: str
    session_id: str
    status: RunStatus
    request_text: str
    active_workflow_id: str | None
    completed_workflow_ids: tuple[str, ...] = ()
    pending_workflow_ids: tuple[str, ...] = ()
    artifacts: tuple[ArtifactSummary, ...] = ()
    verifier_status: str | None = None
    resume_summary: ResumeSummary | None = None
    failure: FailureSummary | None = None
    output: RunOutput | None = None
    revision: int = 0

    def __post_init__(self) -> None:
        _required_identifier(self.tenant_id, "tenant_id")
        _required_identifier(self.run_id, "run_id")
        _required_identifier(self.session_id, "session_id")
        object.__setattr__(self, "status", RunStatus(self.status))
        if not isinstance(self.request_text, str):
            raise ValueError("request_text must be a string")
        _optional_identifier(self.active_workflow_id, "active_workflow_id")
        completed = tuple(_required_identifier(item, "completed_workflow_id") for item in self.completed_workflow_ids)
        pending = tuple(_required_identifier(item, "pending_workflow_id") for item in self.pending_workflow_ids)
        if len(set(completed)) != len(completed) or len(set(pending)) != len(pending):
            raise ValueError("workflow id projections must not contain duplicates")
        if set(completed) & set(pending):
            raise ValueError("completed and pending workflow projections must be disjoint")
        object.__setattr__(self, "completed_workflow_ids", completed)
        object.__setattr__(self, "pending_workflow_ids", pending)
        artifacts = tuple(self.artifacts)
        if any(not isinstance(artifact, ArtifactSummary) for artifact in artifacts):
            raise TypeError("artifacts must contain ArtifactSummary values")
        object.__setattr__(self, "artifacts", artifacts)
        if self.resume_summary is not None and not isinstance(self.resume_summary, ResumeSummary):
            raise TypeError("resume_summary must be a ResumeSummary or None")
        if self.failure is not None and not isinstance(self.failure, FailureSummary):
            raise TypeError("failure must be a FailureSummary or None")
        if self.output is not None and not isinstance(self.output, RunOutput):
            raise TypeError("output must be a RunOutput or None")
        if self.verifier_status is not None and not isinstance(self.verifier_status, str):
            raise ValueError("verifier_status must be a string or None")
        _nonnegative_integer(self.revision, "revision")

    @property
    def failure_summary(self) -> FailureSummary | None:
        """Compatibility alias for the stable failure projection."""

        return self.failure

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "tenant_id": self.tenant_id,
            "run_id": self.run_id,
            "session_id": self.session_id,
            "status": self.status.value,
            "request_text": self.request_text,
            "active_workflow_id": self.active_workflow_id,
            "completed_workflow_ids": list(self.completed_workflow_ids),
            "pending_workflow_ids": list(self.pending_workflow_ids),
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "verifier_status": self.verifier_status,
            "resume_summary": (
                self.resume_summary.to_dict() if self.resume_summary else None
            ),
            "failure": self.failure.to_dict() if self.failure else None,
            "output": self.output.to_dict() if self.output else None,
            "revision": self.revision,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RunSnapshot":
        return cls(
            tenant_id=str(value.get("tenant_id", "")),
            run_id=str(value.get("run_id", "")),
            session_id=str(value.get("session_id", "")),
            status=RunStatus(str(value.get("status", ""))),
            request_text=str(value.get("request_text", "")),
            active_workflow_id=(
                None
                if value.get("active_workflow_id") is None
                else str(value.get("active_workflow_id"))
            ),
            completed_workflow_ids=tuple(
                str(item)
                for item in cast(Sequence[Any], value.get("completed_workflow_ids", []) or [])
            ),
            pending_workflow_ids=tuple(
                str(item)
                for item in cast(Sequence[Any], value.get("pending_workflow_ids", []) or [])
            ),
            artifacts=tuple(
                ArtifactSummary.from_dict(cast(Mapping[str, Any], item))
                for item in cast(Sequence[Any], value.get("artifacts", []) or [])
            ),
            verifier_status=(
                None
                if value.get("verifier_status") is None
                else str(value.get("verifier_status"))
            ),
            resume_summary=(
                None
                if value.get("resume_summary") is None
                else ResumeSummary.from_dict(
                    cast(Mapping[str, Any], value["resume_summary"])
                )
            ),
            failure=(
                None
                if value.get("failure") is None
                else FailureSummary.from_dict(cast(Mapping[str, Any], value["failure"]))
            ),
            output=(
                None
                if value.get("output") is None
                else RunOutput.from_dict(cast(Mapping[str, Any], value["output"]))
            ),
            revision=int(value.get("revision", 0)),
        )


@dataclass(frozen=True)
class RunEvent:
    """Persistable public event; payload is JSON-only and immutable."""

    event_id: str
    sequence_number: int
    tenant_id: str
    session_id: str
    run_id: str
    workflow_id: str | None
    stage_id: str | None
    task_id: str | None
    event_type: EventType
    timestamp: str
    payload: Mapping[str, JSONValue] = field(default_factory=dict)
    run_revision: int = 0

    def __post_init__(self) -> None:
        _required_identifier(self.event_id, "event_id")
        _required_identifier(self.tenant_id, "tenant_id")
        _required_identifier(self.session_id, "session_id")
        _required_identifier(self.run_id, "run_id")
        _optional_identifier(self.workflow_id, "workflow_id")
        _optional_identifier(self.stage_id, "stage_id")
        _optional_identifier(self.task_id, "task_id")
        object.__setattr__(self, "event_type", EventType(self.event_type))
        _positive_integer(self.sequence_number, "sequence_number")
        _nonnegative_integer(self.run_revision, "run_revision")
        if not isinstance(self.timestamp, str) or not self.timestamp.strip():
            raise ValueError("timestamp must be a non-empty string")
        object.__setattr__(self, "payload", _freeze_object(self.payload or {}, "payload"))

    @property
    def is_terminal(self) -> bool:
        return self.event_type.is_terminal

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "event_id": self.event_id,
            "sequence_number": self.sequence_number,
            "tenant_id": self.tenant_id,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "workflow_id": self.workflow_id,
            "stage_id": self.stage_id,
            "task_id": self.task_id,
            "event_type": self.event_type.value,
            "timestamp": self.timestamp,
            "payload": _thaw_json(self.payload),
            "run_revision": self.run_revision,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RunEvent":
        return cls(
            event_id=str(value.get("event_id", "")),
            sequence_number=int(value.get("sequence_number", 0)),
            tenant_id=str(value.get("tenant_id", "")),
            session_id=str(value.get("session_id", "")),
            run_id=str(value.get("run_id", "")),
            workflow_id=(
                None if value.get("workflow_id") is None else str(value.get("workflow_id"))
            ),
            stage_id=None if value.get("stage_id") is None else str(value.get("stage_id")),
            task_id=None if value.get("task_id") is None else str(value.get("task_id")),
            event_type=EventType(str(value.get("event_type", ""))),
            timestamp=str(value.get("timestamp", "")),
            payload=cast(Mapping[str, JSONValue], value.get("payload", {}) or {}),
            run_revision=int(value.get("run_revision", 0)),
        )


class AgentService(Protocol):
    """Pure Python service boundary; implementation starts in v2.3C-2."""

    async def start_run(self, request: StartRunRequest) -> RunHandle:
        ...

    async def get_run(self, request: RunLookupRequest) -> RunSnapshot:
        ...

    async def resume_run(self, request: ResumeRunRequest) -> RunHandle:
        ...

    async def cancel_run(self, request: "CancelRunRequest") -> RunSnapshot:
        ...

    async def list_artifacts(
        self, request: RunLookupRequest
    ) -> tuple[ArtifactView, ...]:
        ...

    def stream_events(self, request: EventStreamRequest) -> AsyncIterator[RunEvent]:
        ...


__all__ = [
    "AgentService",
    "ArtifactSummary",
    "ArtifactView",
    "EventStreamRequest",
    "EventType",
    "FailureSummary",
    "JSONValue",
    "ResumeAction",
    "ResumeDisposition",
    "ResumeRunRequest",
    "ResumeSummary",
    "RunEvent",
    "RunHandle",
    "RunLookupRequest",
    "RunOutput",
    "RunSnapshot",
    "RunStatus",
    "StartRunRequest",
]
