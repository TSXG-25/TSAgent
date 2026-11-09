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


@dataclass(frozen=True)
class ExternalStateEvidence:
    """Caller-supplied observation for one checkpoint-bound external guard."""

    resource_id: str
    guard_type: str
    expected_value: str = ""
    observed_value: str = ""
    checked_at: str = ""
    status: str = "VERIFIED"

    def __post_init__(self) -> None:
        _required_identifier(self.resource_id, "resource_id")
        _required_identifier(self.guard_type, "guard_type")
        for name in ("expected_value", "observed_value", "checked_at"):
            if not isinstance(getattr(self, name), str):
                raise ValueError(f"{name} must be a string")
        if self.status not in {"VERIFIED", "UNKNOWN", "MISMATCH", "MISSING"}:
            raise ValueError("status must be a known external guard status")

    def to_dict(self) -> dict[str, str]:
        return {
            "resource_id": self.resource_id,
            "guard_type": self.guard_type,
            "expected_value": self.expected_value,
            "observed_value": self.observed_value,
            "checked_at": self.checked_at,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ExternalStateEvidence":
        return cls(
            resource_id=str(value.get("resource_id", "")),
            guard_type=str(value.get("guard_type", "")),
            expected_value=str(value.get("expected_value", "")),
            observed_value=str(value.get("observed_value", "")),
            checked_at=str(value.get("checked_at", "")),
            status=str(value.get("status", "VERIFIED")),
        )


@dataclass(frozen=True)
class NoEffectConfirmation:
    """User attestation that one identified external effect did not occur."""

    task_id: str
    idempotency_key: str
    external_reference: str
    checked_at: str

    def __post_init__(self) -> None:
        for name in ("task_id", "idempotency_key", "external_reference", "checked_at"):
            object.__setattr__(self, name, _required_identifier(getattr(self, name), name))

    def to_dict(self) -> dict[str, str]:
        return {
            "task_id": self.task_id,
            "idempotency_key": self.idempotency_key,
            "external_reference": self.external_reference,
            "checked_at": self.checked_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "NoEffectConfirmation":
        return cls(
            task_id=str(value.get("task_id", "")),
            idempotency_key=str(value.get("idempotency_key", "")),
            external_reference=str(value.get("external_reference", "")),
            checked_at=str(value.get("checked_at", "")),
        )


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
    ASSISTANT_DELTA = "assistant_delta"
    ARTIFACT_PUBLISHED = "artifact_published"
    CHECKPOINT_COMMITTED = "checkpoint_committed"
    RESUME_DECIDED = "resume_decided"
    RUN_CANCELLING = "run_cancelling"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"
    RUN_BLOCKED = "run_blocked"
    RUN_WAITING_USER = "run_waiting_user"
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
    """Request to start or idempotently address one logical Run.

    ``run_id`` is optional because the Service normally allocates it.  It is
    retained as an optional replay/fixture hint, but is excluded from the
    request digest so it cannot weaken ``tenant_id + request_id`` idempotency.
    """

    tenant_id: str
    user_id: str
    session_id: str
    request_id: str
    request_text: str
    run_id: str | None = None
    task_id: str = ""
    workspace_root: str = ""
    context_refs: tuple[str, ...] = ()
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.run_id is not None:
            _required_identifier(self.run_id, "run_id")
        if self.task_id:
            _required_identifier(self.task_id, "task_id")
        _validate_identity(
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            session_id=self.session_id,
            run_id=self.run_id or "run-pending",
            request_id=self.request_id,
        )
        if not isinstance(self.request_text, str) or not self.request_text.strip():
            raise ValueError("request_text must be a non-empty string")
        object.__setattr__(self, "metadata", _freeze_object(self.metadata or {}, "metadata"))

    def to_dict(self) -> dict[str, JSONValue]:
        value: dict[str, JSONValue] = {
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "request_id": self.request_id,
            "request_text": self.request_text,
            "metadata": _thaw_json(self.metadata),
        }
        if self.run_id is not None:
            value["run_id"] = self.run_id
        if self.task_id:
            value["task_id"] = self.task_id
        if self.context_refs:
            value["context_refs"] = list(self.context_refs)
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "StartRunRequest":
        return cls(
            tenant_id=str(value.get("tenant_id", "")),
            user_id=str(value.get("user_id", "")),
            session_id=str(value.get("session_id", "")),
            request_id=str(value.get("request_id", "")),
            request_text=str(value.get("request_text", "")),
            run_id=(None if value.get("run_id") is None else str(value.get("run_id"))),
            task_id=str(value.get("task_id", "")),
            workspace_root=str(value.get("workspace_root", "")),
            context_refs=tuple(
                str(item) for item in (value.get("context_refs") or ())
            ),
            metadata=cast(Mapping[str, JSONValue], value.get("metadata", {}) or {}),
        )

    @property
    def request_digest(self) -> str:
        payload = self.to_dict()
        payload.pop("run_id", None)
        return _canonical_digest(payload)


@dataclass(frozen=True)
class ResumeRunRequest:
    """Request to resume an existing logical Run.

    ``request_id`` is the durable resume request identity (also exposed as
    ``resume_request_id``) and must be persisted by the eventual Service Core.
    """

    tenant_id: str
    user_id: str
    session_id: str
    run_id: str
    request_id: str
    request_text: str = ""
    checkpoint_id: str | None = None
    action: ResumeAction | None = None
    external_state_evidence: tuple[ExternalStateEvidence, ...] = ()
    no_effect_confirmation: NoEffectConfirmation | None = None

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
        _optional_identifier(self.checkpoint_id, "checkpoint_id")
        if self.action is not None:
            object.__setattr__(self, "action", ResumeAction(self.action))
        object.__setattr__(
            self,
            "external_state_evidence",
            tuple(
                item if isinstance(item, ExternalStateEvidence)
                else ExternalStateEvidence.from_dict(cast(Mapping[str, Any], item))
                for item in (self.external_state_evidence or ())
            ),
        )
        if self.no_effect_confirmation is not None and not isinstance(
            self.no_effect_confirmation, NoEffectConfirmation
        ):
            object.__setattr__(
                self,
                "no_effect_confirmation",
                NoEffectConfirmation.from_dict(
                    cast(Mapping[str, Any], self.no_effect_confirmation)
                ),
            )

    @property
    def resume_request_id(self) -> str:
        return self.request_id

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "request_id": self.request_id,
            "request_text": self.request_text,
            "checkpoint_id": self.checkpoint_id,
            "action": self.action.value if self.action else None,
            "external_state_evidence": [
                item.to_dict() for item in self.external_state_evidence
            ],
            "no_effect_confirmation": (
                self.no_effect_confirmation.to_dict()
                if self.no_effect_confirmation is not None
                else None
            ),
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
            checkpoint_id=(
                None if value.get("checkpoint_id") is None else str(value.get("checkpoint_id"))
            ),
            action=(
                None
                if value.get("action") is None
                else ResumeAction(str(value.get("action")))
            ),
            external_state_evidence=tuple(
                ExternalStateEvidence.from_dict(item)
                for item in value.get("external_state_evidence", []) or ()
            ),
            no_effect_confirmation=(
                None
                if value.get("no_effect_confirmation") is None
                else NoEffectConfirmation.from_dict(
                    cast(Mapping[str, Any], value["no_effect_confirmation"])
                )
            ),
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
class ListRunsRequest:
    """Identity-complete request for the Runs of one session."""

    tenant_id: str
    user_id: str
    session_id: str
    request_id: str
    limit: int = 100

    def __post_init__(self) -> None:
        _required_identifier(self.tenant_id, "tenant_id")
        _required_identifier(self.user_id, "user_id")
        _required_identifier(self.session_id, "session_id")
        _required_identifier(self.request_id, "request_id")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ListRunsRequest":
        return cls(
            tenant_id=str(value.get("tenant_id", "")),
            user_id=str(value.get("user_id", "")),
            session_id=str(value.get("session_id", "")),
            request_id=str(value.get("request_id", "")),
            limit=int(value.get("limit") or 100),
        )


@dataclass(frozen=True)
class CreateTaskRequest:
    """Request to create one Task (delegation/tree unit) in a session."""

    tenant_id: str
    user_id: str
    session_id: str
    request_id: str
    parent_task_id: str = ""
    origin_planet: str = ""
    owner_profile_id: str = ""
    task_id: str = ""

    def __post_init__(self) -> None:
        _required_identifier(self.tenant_id, "tenant_id")
        _required_identifier(self.user_id, "user_id")
        _required_identifier(self.session_id, "session_id")
        _required_identifier(self.request_id, "request_id")
        if self.task_id:
            _required_identifier(self.task_id, "task_id")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CreateTaskRequest":
        return cls(
            tenant_id=str(value.get("tenant_id", "")),
            user_id=str(value.get("user_id", "")),
            session_id=str(value.get("session_id", "")),
            request_id=str(value.get("request_id", "")),
            parent_task_id=str(value.get("parent_task_id", "")),
            origin_planet=str(value.get("origin_planet", "")),
            owner_profile_id=str(value.get("owner_profile_id", "")),
            task_id=str(value.get("task_id", "")),
        )


@dataclass(frozen=True)
class ListTasksRequest:
    """Identity-complete request for the Tasks of one session."""

    tenant_id: str
    user_id: str
    session_id: str
    request_id: str
    limit: int = 500

    def __post_init__(self) -> None:
        _required_identifier(self.tenant_id, "tenant_id")
        _required_identifier(self.user_id, "user_id")
        _required_identifier(self.session_id, "session_id")
        _required_identifier(self.request_id, "request_id")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ListTasksRequest":
        return cls(
            tenant_id=str(value.get("tenant_id", "")),
            user_id=str(value.get("user_id", "")),
            session_id=str(value.get("session_id", "")),
            request_id=str(value.get("request_id", "")),
            limit=int(value.get("limit") or 500),
        )


@dataclass(frozen=True)
class TaskLookupRequest:
    """Identity-complete request for one Task snapshot."""

    tenant_id: str
    user_id: str
    session_id: str
    request_id: str
    task_id: str

    def __post_init__(self) -> None:
        _required_identifier(self.tenant_id, "tenant_id")
        _required_identifier(self.user_id, "user_id")
        _required_identifier(self.session_id, "session_id")
        _required_identifier(self.request_id, "request_id")
        _required_identifier(self.task_id, "task_id")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TaskLookupRequest":
        return cls(
            tenant_id=str(value.get("tenant_id", "")),
            user_id=str(value.get("user_id", "")),
            session_id=str(value.get("session_id", "")),
            request_id=str(value.get("request_id", "")),
            task_id=str(value.get("task_id", "")),
        )


@dataclass(frozen=True)
class TaskHandle:
    """Public handle for one Task."""

    tenant_id: str
    session_id: str
    task_id: str
    parent_task_id: str
    origin_planet: str
    owner_profile_id: str
    created_at: str

    def to_dict(self) -> dict[str, str]:
        return {
            "tenant_id": self.tenant_id,
            "session_id": self.session_id,
            "task_id": self.task_id,
            "parent_task_id": self.parent_task_id,
            "origin_planet": self.origin_planet,
            "owner_profile_id": self.owner_profile_id,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class TaskSnapshot(TaskHandle):
    """Task head plus derived run tree state."""

    status: str = "CREATED"
    run_ids: tuple[str, ...] = ()
    child_task_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, JSONValue]:
        value: dict[str, JSONValue] = dict(super().to_dict())
        value["status"] = self.status
        value["run_ids"] = list(self.run_ids)
        value["child_task_ids"] = list(self.child_task_ids)
        return value


@dataclass(frozen=True)
class CreateSessionRequest:
    """Request to register a session explicitly (auto-created on first run)."""

    tenant_id: str
    user_id: str
    session_id: str
    request_id: str

    def __post_init__(self) -> None:
        _required_identifier(self.tenant_id, "tenant_id")
        _required_identifier(self.user_id, "user_id")
        _required_identifier(self.session_id, "session_id")
        _required_identifier(self.request_id, "request_id")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CreateSessionRequest":
        return cls(
            tenant_id=str(value.get("tenant_id", "")),
            user_id=str(value.get("user_id", "")),
            session_id=str(value.get("session_id", "")),
            request_id=str(value.get("request_id", "")),
        )


@dataclass(frozen=True)
class DeleteSessionRequest:
    """Request to remove a session from the registry. Orphaned runs remain
    addressable by session locator but the session no longer lists."""

    tenant_id: str
    user_id: str
    session_id: str
    request_id: str

    def __post_init__(self) -> None:
        _required_identifier(self.tenant_id, "tenant_id")
        _required_identifier(self.user_id, "user_id")
        _required_identifier(self.session_id, "session_id")
        _required_identifier(self.request_id, "request_id")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DeleteSessionRequest":
        return cls(
            tenant_id=str(value.get("tenant_id", "")),
            user_id=str(value.get("user_id", "")),
            session_id=str(value.get("session_id", "")),
            request_id=str(value.get("request_id", "")),
        )


@dataclass(frozen=True)
class ListSessionsRequest:
    tenant_id: str
    user_id: str
    request_id: str
    limit: int = 500

    def __post_init__(self) -> None:
        _required_identifier(self.tenant_id, "tenant_id")
        _required_identifier(self.user_id, "user_id")
        _required_identifier(self.request_id, "request_id")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ListSessionsRequest":
        return cls(
            tenant_id=str(value.get("tenant_id", "")),
            user_id=str(value.get("user_id", "")),
            request_id=str(value.get("request_id", "")),
            limit=int(value.get("limit") or 500),
        )


@dataclass(frozen=True)
class SessionSummary:
    """Public session handle plus derived run activity."""

    tenant_id: str
    session_id: str
    user_id: str
    created_at: str
    run_count: int = 0
    latest_run_id: str = ""
    latest_status: str = ""
    latest_updated_at: str = ""
    workspace_id: str = ""

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "tenant_id": self.tenant_id,
            "workspace_id": self.workspace_id,
            "session_id": self.session_id,
            "user_id": self.user_id,
            "created_at": self.created_at,
            "run_count": self.run_count,
            "latest_run_id": self.latest_run_id,
            "latest_status": self.latest_status,
            "latest_updated_at": self.latest_updated_at,
        }


@dataclass(frozen=True)
class HistoryEntry:
    """One durable history row: a completed request/answer with provenance."""

    run_id: str
    task_id: str
    request_text: str
    answer_text: str
    status: str
    updated_at: str

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "request_text": self.request_text,
            "answer_text": self.answer_text,
            "status": self.status,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class ListHistoryRequest:
    """Cross-run history query for one session."""

    tenant_id: str
    user_id: str
    session_id: str
    request_id: str
    limit: int = 200

    def __post_init__(self) -> None:
        _required_identifier(self.tenant_id, "tenant_id")
        _required_identifier(self.user_id, "user_id")
        _required_identifier(self.session_id, "session_id")
        _required_identifier(self.request_id, "request_id")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ListHistoryRequest":
        return cls(
            tenant_id=str(value.get("tenant_id", "")),
            user_id=str(value.get("user_id", "")),
            session_id=str(value.get("session_id", "")),
            request_id=str(value.get("request_id", "")),
            limit=int(value.get("limit") or 200),
        )


@dataclass(frozen=True)
class ScheduleSummary:
    """Public schedule card for one recurring task."""

    schedule_id: str
    session_id: str
    request_text: str
    user_id: str
    auto_eval: bool
    target_planet: str
    interval_seconds: int
    status: str
    next_fire_at: str
    last_fired_at: str
    last_run_id: str
    fire_count: int
    created_at: str

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "schedule_id": self.schedule_id,
            "session_id": self.session_id,
            "request_text": self.request_text,
            "user_id": self.user_id,
            "auto_eval": self.auto_eval,
            "target_planet": self.target_planet,
            "interval_seconds": self.interval_seconds,
            "status": self.status,
            "next_fire_at": self.next_fire_at,
            "last_fired_at": self.last_fired_at,
            "last_run_id": self.last_run_id,
            "fire_count": self.fire_count,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class CreateScheduleRequest:
    tenant_id: str
    user_id: str
    session_id: str
    request_id: str
    request_text: str
    interval_seconds: int
    target_planet: str = ""
    schedule_id: str = ""
    auto_eval: bool = False

    def __post_init__(self) -> None:
        _required_identifier(self.tenant_id, "tenant_id")
        _required_identifier(self.user_id, "user_id")
        _required_identifier(self.session_id, "session_id")
        _required_identifier(self.request_id, "request_id")
        if not self.request_text.strip():
            raise ValueError("request_text must be a non-empty string")
        if int(self.interval_seconds) < 1:
            raise ValueError("interval_seconds must be positive")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CreateScheduleRequest":
        return cls(
            tenant_id=str(value.get("tenant_id", "")),
            user_id=str(value.get("user_id", "")),
            session_id=str(value.get("session_id", "")),
            request_id=str(value.get("request_id", "")),
            request_text=str(value.get("request_text", "")),
            interval_seconds=int(value.get("interval_seconds", 0)),
            target_planet=str(value.get("target_planet", "")),
            schedule_id=str(value.get("schedule_id", "")),
            auto_eval=bool(value.get("auto_eval", False)),
        )


@dataclass(frozen=True)
class ListSchedulesRequest:
    tenant_id: str
    user_id: str
    session_id: str
    request_id: str
    limit: int = 200

    def __post_init__(self) -> None:
        _required_identifier(self.tenant_id, "tenant_id")
        _required_identifier(self.user_id, "user_id")
        _required_identifier(self.session_id, "session_id")
        _required_identifier(self.request_id, "request_id")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ListSchedulesRequest":
        return cls(
            tenant_id=str(value.get("tenant_id", "")),
            user_id=str(value.get("user_id", "")),
            session_id=str(value.get("session_id", "")),
            request_id=str(value.get("request_id", "")),
            limit=int(value.get("limit") or 200),
        )


@dataclass(frozen=True)
class UpdateScheduleRequest:
    tenant_id: str
    user_id: str
    session_id: str
    request_id: str
    schedule_id: str
    status: str  # ACTIVE | PAUSED | DELETED

    def __post_init__(self) -> None:
        _required_identifier(self.tenant_id, "tenant_id")
        _required_identifier(self.user_id, "user_id")
        _required_identifier(self.session_id, "session_id")
        _required_identifier(self.request_id, "request_id")
        _required_identifier(self.schedule_id, "schedule_id")
        if self.status not in ("ACTIVE", "PAUSED", "DELETED"):
            raise ValueError("status must be ACTIVE, PAUSED or DELETED")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "UpdateScheduleRequest":
        return cls(
            tenant_id=str(value.get("tenant_id", "")),
            user_id=str(value.get("user_id", "")),
            session_id=str(value.get("session_id", "")),
            request_id=str(value.get("request_id", "")),
            schedule_id=str(value.get("schedule_id", "")),
            status=str(value.get("status", "")).upper(),
        )


@dataclass(frozen=True)
class ProfileSummary:
    """Swappable agent binding for a planet/scene."""

    profile_id: str
    display_name: str
    planet_affinity: str
    model_ref: str
    skill_refs: tuple[str, ...]
    permission_policy: Mapping[str, JSONValue]
    updated_at: str

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "profile_id": self.profile_id,
            "display_name": self.display_name,
            "planet_affinity": self.planet_affinity,
            "model_ref": self.model_ref,
            "skill_refs": list(self.skill_refs),
            "permission_policy": _thaw_json(self.permission_policy),
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class UpsertProfileRequest:
    tenant_id: str
    user_id: str
    session_id: str
    request_id: str
    profile_id: str
    display_name: str = ""
    planet_affinity: str = ""
    model_ref: str = ""
    skill_refs: tuple[str, ...] = ()
    permission_policy: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _required_identifier(self.tenant_id, "tenant_id")
        _required_identifier(self.user_id, "user_id")
        _required_identifier(self.session_id, "session_id")
        _required_identifier(self.request_id, "request_id")
        _required_identifier(self.profile_id, "profile_id")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "UpsertProfileRequest":
        return cls(
            tenant_id=str(value.get("tenant_id", "")),
            user_id=str(value.get("user_id", "")),
            session_id=str(value.get("session_id", "")),
            request_id=str(value.get("request_id", "")),
            profile_id=str(value.get("profile_id", "")),
            display_name=str(value.get("display_name", "")),
            planet_affinity=str(value.get("planet_affinity", "")),
            model_ref=str(value.get("model_ref", "")),
            skill_refs=tuple(str(v) for v in (value.get("skill_refs") or ())),
            permission_policy=cast(Mapping[str, JSONValue], value.get("permission_policy") or {}),
        )


@dataclass(frozen=True)
class ListProfilesRequest:
    tenant_id: str
    user_id: str
    session_id: str
    request_id: str
    limit: int = 200

    def __post_init__(self) -> None:
        _required_identifier(self.tenant_id, "tenant_id")
        _required_identifier(self.user_id, "user_id")
        _required_identifier(self.session_id, "session_id")
        _required_identifier(self.request_id, "request_id")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ListProfilesRequest":
        return cls(
            tenant_id=str(value.get("tenant_id", "")),
            user_id=str(value.get("user_id", "")),
            session_id=str(value.get("session_id", "")),
            request_id=str(value.get("request_id", "")),
            limit=int(value.get("limit") or 200),
        )


@dataclass(frozen=True)
class SettingsSummary:
    """Read-only runtime/provider surface (Settings tab)."""

    provider_mode: str
    active_provider: str
    active_model: str
    deepseek_model: str
    ollama_model: str

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "provider_mode": self.provider_mode,
            "active_provider": self.active_provider,
            "active_model": self.active_model,
            "deepseek_model": self.deepseek_model,
            "ollama_model": self.ollama_model,
        }


@dataclass(frozen=True)
class GetSettingsRequest:
    tenant_id: str
    user_id: str
    session_id: str
    request_id: str

    def __post_init__(self) -> None:
        _required_identifier(self.tenant_id, "tenant_id")
        _required_identifier(self.user_id, "user_id")
        _required_identifier(self.session_id, "session_id")
        _required_identifier(self.request_id, "request_id")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "GetSettingsRequest":
        return cls(
            tenant_id=str(value.get("tenant_id", "")),
            user_id=str(value.get("user_id", "")),
            session_id=str(value.get("session_id", "")),
            request_id=str(value.get("request_id", "")),
        )


@dataclass(frozen=True)
class UpdateSettingsRequest:
    tenant_id: str
    user_id: str
    session_id: str
    request_id: str
    provider_mode: str = ""
    deepseek_model: str = ""
    ollama_model: str = ""

    def __post_init__(self) -> None:
        _required_identifier(self.tenant_id, "tenant_id")
        _required_identifier(self.user_id, "user_id")
        _required_identifier(self.session_id, "session_id")
        _required_identifier(self.request_id, "request_id")
        if self.provider_mode and self.provider_mode not in (
            "auto", "deepseek", "ollama"
        ):
            raise ValueError(
                "provider_mode must be auto, deepseek or ollama"
            )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "UpdateSettingsRequest":
        return cls(
            tenant_id=str(value.get("tenant_id", "")),
            user_id=str(value.get("user_id", "")),
            session_id=str(value.get("session_id", "")),
            request_id=str(value.get("request_id", "")),
            provider_mode=str(value.get("provider_mode", "")),
            deepseek_model=str(value.get("deepseek_model", "")),
            ollama_model=str(value.get("ollama_model", "")),
        )


@dataclass(frozen=True)
class WorkspaceSummary:
    workspace_id: str
    display_name: str
    capabilities: tuple[str, ...]
    default_profile_id: str
    description: str
    updated_at: str
    path: str = ""
    paths: tuple[str, ...] = ()
    member_count: int = 0

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "workspace_id": self.workspace_id,
            "display_name": self.display_name,
            "path": self.path,
            "paths": list(self.paths),
            "member_count": self.member_count,
            "capabilities": list(self.capabilities),
            "default_profile_id": self.default_profile_id,
            "description": self.description,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class UpsertWorkspaceRequest:
    tenant_id: str
    user_id: str
    session_id: str
    request_id: str
    workspace_id: str
    display_name: str = ""
    path: str = ""
    paths: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    default_profile_id: str = ""
    description: str = ""

    def __post_init__(self) -> None:
        _required_identifier(self.tenant_id, "tenant_id")
        _required_identifier(self.user_id, "user_id")
        _required_identifier(self.session_id, "session_id")
        _required_identifier(self.request_id, "request_id")
        _required_identifier(self.workspace_id, "workspace_id")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "UpsertWorkspaceRequest":
        return cls(
            tenant_id=str(value.get("tenant_id", "")),
            user_id=str(value.get("user_id", "")),
            session_id=str(value.get("session_id", "")),
            request_id=str(value.get("request_id", "")),
            workspace_id=str(value.get("workspace_id", "")),
            display_name=str(value.get("display_name", "")),
            path=str(value.get("path", "")),
            paths=tuple(str(v) for v in (value.get("paths") or ())),
            capabilities=tuple(str(v) for v in (value.get("capabilities") or ())),
            default_profile_id=str(value.get("default_profile_id", "")),
            description=str(value.get("description", "")),
        )


@dataclass(frozen=True)
class ListWorkspacesRequest:
    tenant_id: str
    user_id: str
    session_id: str
    request_id: str
    limit: int = 50

    def __post_init__(self) -> None:
        _required_identifier(self.tenant_id, "tenant_id")
        _required_identifier(self.user_id, "user_id")
        _required_identifier(self.session_id, "session_id")
        _required_identifier(self.request_id, "request_id")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ListWorkspacesRequest":
        return cls(
            tenant_id=str(value.get("tenant_id", "")),
            user_id=str(value.get("user_id", "")),
            session_id=str(value.get("session_id", "")),
            request_id=str(value.get("request_id", "")),
            limit=int(value.get("limit") or 50),
        )


@dataclass(frozen=True)
class PauseRunRequest:
    """Identity-complete request to suspend a locally-executing run."""

    tenant_id: str
    user_id: str
    session_id: str
    run_id: str
    request_id: str
    requested_by: str = ""

    def __post_init__(self) -> None:
        _required_identifier(self.tenant_id, "tenant_id")
        _required_identifier(self.user_id, "user_id")
        _required_identifier(self.session_id, "session_id")
        _required_identifier(self.run_id, "run_id")
        _required_identifier(self.request_id, "request_id")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PauseRunRequest":
        return cls(
            tenant_id=str(value.get("tenant_id", "")),
            user_id=str(value.get("user_id", "")),
            session_id=str(value.get("session_id", "")),
            run_id=str(value.get("run_id", "")),
            request_id=str(value.get("request_id", "")),
            requested_by=str(value.get("requested_by", "")),
        )


@dataclass(frozen=True)
class FactRecordSummary:
    """One remembered fact with governance provenance."""

    category: str
    key: str
    value: str
    scope: str
    evidence_id: str
    source_kind: str
    source_ref: str
    revision: int
    created_at: str

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "category": self.category,
            "key": self.key,
            "value": self.value,
            "scope": self.scope,
            "evidence_id": self.evidence_id,
            "source_kind": self.source_kind,
            "source_ref": self.source_ref,
            "revision": self.revision,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class FactForgetResult:
    category: str
    key: str
    removed: bool

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "category": self.category,
            "key": self.key,
            "removed": self.removed,
        }


@dataclass(frozen=True)
class ListFactsRequest:
    tenant_id: str
    user_id: str
    session_id: str
    request_id: str
    scope: str = ""
    limit: int = 500

    def __post_init__(self) -> None:
        _required_identifier(self.tenant_id, "tenant_id")
        _required_identifier(self.user_id, "user_id")
        _required_identifier(self.session_id, "session_id")
        _required_identifier(self.request_id, "request_id")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ListFactsRequest":
        return cls(
            tenant_id=str(value.get("tenant_id", "")),
            user_id=str(value.get("user_id", "")),
            session_id=str(value.get("session_id", "")),
            request_id=str(value.get("request_id", "")),
            scope=str(value.get("scope", "")),
            limit=int(value.get("limit") or 500),
        )


@dataclass(frozen=True)
class ForgetFactRequest:
    tenant_id: str
    user_id: str
    session_id: str
    request_id: str
    category: str
    key: str
    scope: str = ""

    def __post_init__(self) -> None:
        _required_identifier(self.tenant_id, "tenant_id")
        _required_identifier(self.user_id, "user_id")
        _required_identifier(self.session_id, "session_id")
        _required_identifier(self.request_id, "request_id")
        _required_identifier(self.category, "category")
        _required_identifier(self.key, "key")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ForgetFactRequest":
        return cls(
            tenant_id=str(value.get("tenant_id", "")),
            user_id=str(value.get("user_id", "")),
            session_id=str(value.get("session_id", "")),
            request_id=str(value.get("request_id", "")),
            category=str(value.get("category", "")),
            key=str(value.get("key", "")),
            scope=str(value.get("scope", "")),
        )


@dataclass(frozen=True)
class ParticipantSummary:
    """One session participant: a user or an agent identity."""

    participant_id: str
    role: str
    joined_at: str

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "participant_id": self.participant_id,
            "role": self.role,
            "joined_at": self.joined_at,
        }


@dataclass(frozen=True)
class ListParticipantsRequest:
    tenant_id: str
    user_id: str
    session_id: str
    request_id: str

    def __post_init__(self) -> None:
        _required_identifier(self.tenant_id, "tenant_id")
        _required_identifier(self.user_id, "user_id")
        _required_identifier(self.session_id, "session_id")
        _required_identifier(self.request_id, "request_id")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ListParticipantsRequest":
        return cls(
            tenant_id=str(value.get("tenant_id", "")),
            user_id=str(value.get("user_id", "")),
            session_id=str(value.get("session_id", "")),
            request_id=str(value.get("request_id", "")),
        )


@dataclass(frozen=True)
class JupiterArtifactSummary:
    """Cross-session artifact index row (Jupiter domain)."""

    session_id: str
    run_id: str
    artifact_id: str
    artifact_type: str
    reference: str
    digest: str
    exists: bool
    verified: bool
    producer_task_id: str
    updated_at: str

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "session_id": self.session_id,
            "run_id": self.run_id,
            "artifact_id": self.artifact_id,
            "artifact_type": self.artifact_type,
            "reference": self.reference,
            "digest": self.digest,
            "exists": self.exists,
            "verified": self.verified,
            "producer_task_id": self.producer_task_id,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class SearchArtifactsRequest:
    tenant_id: str
    user_id: str
    session_id: str
    request_id: str
    query: str = ""
    all_sessions: bool = False
    limit: int = 200

    def __post_init__(self) -> None:
        _required_identifier(self.tenant_id, "tenant_id")
        _required_identifier(self.user_id, "user_id")
        _required_identifier(self.session_id, "session_id")
        _required_identifier(self.request_id, "request_id")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SearchArtifactsRequest":
        return cls(
            tenant_id=str(value.get("tenant_id", "")),
            user_id=str(value.get("user_id", "")),
            session_id=str(value.get("session_id", "")),
            request_id=str(value.get("request_id", "")),
            query=str(value.get("query", "")),
            all_sessions=bool(value.get("all_sessions", False)),
            limit=int(value.get("limit") or 200),
        )


@dataclass(frozen=True)
class DelegateRunRequest:
    """UI-side delegation: provision a child Task + durable Handoff edge in
    one call (the run itself is started via start_run with the task_id)."""

    tenant_id: str
    user_id: str
    session_id: str
    request_id: str
    parent_run_id: str
    request_text: str
    target_planet: str = ""
    profile_id: str = ""
    context_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _required_identifier(self.tenant_id, "tenant_id")
        _required_identifier(self.user_id, "user_id")
        _required_identifier(self.session_id, "session_id")
        _required_identifier(self.request_id, "request_id")
        _required_identifier(self.parent_run_id, "parent_run_id")
        if not self.request_text.strip():
            raise ValueError("request_text must be a non-empty string")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DelegateRunRequest":
        return cls(
            tenant_id=str(value.get("tenant_id", "")),
            user_id=str(value.get("user_id", "")),
            session_id=str(value.get("session_id", "")),
            request_id=str(value.get("request_id", "")),
            parent_run_id=str(value.get("parent_run_id", "")),
            request_text=str(value.get("request_text", "")),
            target_planet=str(value.get("target_planet", "")),
            profile_id=str(value.get("profile_id", "")),
            context_refs=tuple(str(v) for v in (value.get("context_refs") or ())),
        )


@dataclass(frozen=True)
class WorkspaceFileSummary:
    path: str
    size: int
    modified_at: str

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "path": self.path,
            "size": self.size,
            "modified_at": self.modified_at,
        }


@dataclass(frozen=True)
class WorkspaceFileContent:
    path: str
    text: str

    def to_dict(self) -> dict[str, JSONValue]:
        return {"path": self.path, "text": self.text}


@dataclass(frozen=True)
class ListWorkspaceFilesRequest:
    tenant_id: str
    user_id: str
    session_id: str
    request_id: str
    limit: int = 500

    def __post_init__(self) -> None:
        _required_identifier(self.tenant_id, "tenant_id")
        _required_identifier(self.user_id, "user_id")
        _required_identifier(self.session_id, "session_id")
        _required_identifier(self.request_id, "request_id")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ListWorkspaceFilesRequest":
        return cls(
            tenant_id=str(value.get("tenant_id", "")),
            user_id=str(value.get("user_id", "")),
            session_id=str(value.get("session_id", "")),
            request_id=str(value.get("request_id", "")),
            limit=int(value.get("limit") or 500),
        )


@dataclass(frozen=True)
class AttachFilesRequest:
    tenant_id: str
    user_id: str
    session_id: str
    request_id: str
    paths: tuple[str, ...]

    def __post_init__(self) -> None:
        _required_identifier(self.tenant_id, "tenant_id")
        _required_identifier(self.user_id, "user_id")
        _required_identifier(self.session_id, "session_id")
        _required_identifier(self.request_id, "request_id")
        if not self.paths or any(not str(p).strip() for p in self.paths):
            raise ValueError("paths must be a non-empty list of paths")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AttachFilesRequest":
        return cls(
            tenant_id=str(value.get("tenant_id", "")),
            user_id=str(value.get("user_id", "")),
            session_id=str(value.get("session_id", "")),
            request_id=str(value.get("request_id", "")),
            paths=tuple(str(p) for p in (value.get("paths") or [])),
        )


@dataclass(frozen=True)
class WorkspaceFileRequest:
    tenant_id: str
    user_id: str
    session_id: str
    request_id: str
    path: str
    content: str = ""

    def __post_init__(self) -> None:
        _required_identifier(self.tenant_id, "tenant_id")
        _required_identifier(self.user_id, "user_id")
        _required_identifier(self.session_id, "session_id")
        _required_identifier(self.request_id, "request_id")
        if not str(self.path).strip():
            raise ValueError("path must be a non-empty string")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "WorkspaceFileRequest":
        return cls(
            tenant_id=str(value.get("tenant_id", "")),
            user_id=str(value.get("user_id", "")),
            session_id=str(value.get("session_id", "")),
            request_id=str(value.get("request_id", "")),
            path=str(value.get("path", "")),
            content=str(value.get("content", "")),
        )


@dataclass(frozen=True)
class EvaluationSummary:
    eval_id: str
    name: str
    run_id: str
    baseline_ref: str
    candidate_ref: str
    score: float
    verdict: str
    notes: str
    created_at: str

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "eval_id": self.eval_id,
            "name": self.name,
            "run_id": self.run_id,
            "baseline_ref": self.baseline_ref,
            "candidate_ref": self.candidate_ref,
            "score": self.score,
            "verdict": self.verdict,
            "notes": self.notes,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class RecordEvaluationRequest:
    tenant_id: str
    user_id: str
    session_id: str
    request_id: str
    name: str = ""
    run_id: str = ""
    baseline_ref: str = ""
    candidate_ref: str = ""
    score: float = 0.0
    verdict: str = "neutral"
    notes: str = ""
    eval_id: str = ""

    def __post_init__(self) -> None:
        _required_identifier(self.tenant_id, "tenant_id")
        _required_identifier(self.user_id, "user_id")
        _required_identifier(self.session_id, "session_id")
        _required_identifier(self.request_id, "request_id")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RecordEvaluationRequest":
        return cls(
            tenant_id=str(value.get("tenant_id", "")),
            user_id=str(value.get("user_id", "")),
            session_id=str(value.get("session_id", "")),
            request_id=str(value.get("request_id", "")),
            name=str(value.get("name", "")),
            run_id=str(value.get("run_id", "")),
            baseline_ref=str(value.get("baseline_ref", "")),
            candidate_ref=str(value.get("candidate_ref", "")),
            score=float(value.get("score", 0) or 0),
            verdict=str(value.get("verdict", "neutral")),
            notes=str(value.get("notes", "")),
            eval_id=str(value.get("eval_id", "")),
        )


@dataclass(frozen=True)
class ListEvaluationsRequest:
    tenant_id: str
    user_id: str
    session_id: str
    request_id: str
    all_sessions: bool = False
    limit: int = 200

    def __post_init__(self) -> None:
        _required_identifier(self.tenant_id, "tenant_id")
        _required_identifier(self.user_id, "user_id")
        _required_identifier(self.session_id, "session_id")
        _required_identifier(self.request_id, "request_id")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ListEvaluationsRequest":
        return cls(
            tenant_id=str(value.get("tenant_id", "")),
            user_id=str(value.get("user_id", "")),
            session_id=str(value.get("session_id", "")),
            request_id=str(value.get("request_id", "")),
            all_sessions=bool(value.get("all_sessions", False)),
            limit=int(value.get("limit") or 200),
        )


@dataclass(frozen=True)
class WorkflowGraphSummary:
    graph_id: str
    name: str
    definition_json: str
    owner: str
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "graph_id": self.graph_id,
            "name": self.name,
            "definition_json": self.definition_json,
            "owner": self.owner,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class UpsertWorkflowGraphRequest:
    tenant_id: str
    user_id: str
    session_id: str
    request_id: str
    graph_id: str
    name: str = ""
    definition: Mapping[str, Any] = field(default_factory=dict)
    owner: str = ""

    def __post_init__(self) -> None:
        _required_identifier(self.tenant_id, "tenant_id")
        _required_identifier(self.user_id, "user_id")
        _required_identifier(self.session_id, "session_id")
        _required_identifier(self.request_id, "request_id")
        _required_identifier(self.graph_id, "graph_id")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "UpsertWorkflowGraphRequest":
        return cls(
            tenant_id=str(value.get("tenant_id", "")),
            user_id=str(value.get("user_id", "")),
            session_id=str(value.get("session_id", "")),
            request_id=str(value.get("request_id", "")),
            graph_id=str(value.get("graph_id", "")),
            name=str(value.get("name", "")),
            definition=dict(value.get("definition") or {}),
            owner=str(value.get("owner", "")),
        )


@dataclass(frozen=True)
class ListWorkflowGraphsRequest:
    tenant_id: str
    user_id: str
    session_id: str
    request_id: str
    limit: int = 200

    def __post_init__(self) -> None:
        _required_identifier(self.tenant_id, "tenant_id")
        _required_identifier(self.user_id, "user_id")
        _required_identifier(self.session_id, "session_id")
        _required_identifier(self.request_id, "request_id")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ListWorkflowGraphsRequest":
        return cls(
            tenant_id=str(value.get("tenant_id", "")),
            user_id=str(value.get("user_id", "")),
            session_id=str(value.get("session_id", "")),
            request_id=str(value.get("request_id", "")),
            limit=int(value.get("limit") or 200),
        )


@dataclass(frozen=True)
class MotifSummary:
    motif_id: str
    text: str
    source: str
    status: str
    assigned_task_id: str
    resolution_run_id: str
    resolution: str
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "motif_id": self.motif_id,
            "text": self.text,
            "source": self.source,
            "status": self.status,
            "assigned_task_id": self.assigned_task_id,
            "resolution_run_id": self.resolution_run_id,
            "resolution": self.resolution,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class CreateMotifRequest:
    tenant_id: str
    user_id: str
    session_id: str
    request_id: str
    text: str
    motif_id: str = ""
    source: str = "user"

    def __post_init__(self) -> None:
        _required_identifier(self.tenant_id, "tenant_id")
        _required_identifier(self.user_id, "user_id")
        _required_identifier(self.session_id, "session_id")
        _required_identifier(self.request_id, "request_id")
        if not self.text.strip():
            raise ValueError("motif text must be non-empty")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CreateMotifRequest":
        return cls(
            tenant_id=str(value.get("tenant_id", "")),
            user_id=str(value.get("user_id", "")),
            session_id=str(value.get("session_id", "")),
            request_id=str(value.get("request_id", "")),
            text=str(value.get("text", "")),
            motif_id=str(value.get("motif_id", "")),
            source=str(value.get("source", "user")),
        )


@dataclass(frozen=True)
class AssignMotifRequest:
    tenant_id: str
    user_id: str
    session_id: str
    request_id: str
    motif_id: str
    parent_run_id: str = ""

    def __post_init__(self) -> None:
        _required_identifier(self.tenant_id, "tenant_id")
        _required_identifier(self.user_id, "user_id")
        _required_identifier(self.session_id, "session_id")
        _required_identifier(self.request_id, "request_id")
        _required_identifier(self.motif_id, "motif_id")
        _required_identifier(self.parent_run_id, "parent_run_id")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AssignMotifRequest":
        return cls(
            tenant_id=str(value.get("tenant_id", "")),
            user_id=str(value.get("user_id", "")),
            session_id=str(value.get("session_id", "")),
            request_id=str(value.get("request_id", "")),
            motif_id=str(value.get("motif_id", "")),
            parent_run_id=str(value.get("parent_run_id", "")),
        )


@dataclass(frozen=True)
class ResolveMotifRequest:
    tenant_id: str
    user_id: str
    session_id: str
    request_id: str
    motif_id: str
    resolution_run_id: str
    resolution: str

    def __post_init__(self) -> None:
        _required_identifier(self.tenant_id, "tenant_id")
        _required_identifier(self.user_id, "user_id")
        _required_identifier(self.session_id, "session_id")
        _required_identifier(self.request_id, "request_id")
        _required_identifier(self.motif_id, "motif_id")
        _required_identifier(self.resolution_run_id, "resolution_run_id")
        if not self.resolution.strip():
            raise ValueError("resolution must be non-empty")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ResolveMotifRequest":
        return cls(
            tenant_id=str(value.get("tenant_id", "")),
            user_id=str(value.get("user_id", "")),
            session_id=str(value.get("session_id", "")),
            request_id=str(value.get("request_id", "")),
            motif_id=str(value.get("motif_id", "")),
            resolution_run_id=str(value.get("resolution_run_id", "")),
            resolution=str(value.get("resolution", "")),
        )


@dataclass(frozen=True)
class ListMotifsRequest:
    tenant_id: str
    user_id: str
    session_id: str
    request_id: str
    status: str = ""
    limit: int = 100

    def __post_init__(self) -> None:
        _required_identifier(self.tenant_id, "tenant_id")
        _required_identifier(self.user_id, "user_id")
        _required_identifier(self.session_id, "session_id")
        _required_identifier(self.request_id, "request_id")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ListMotifsRequest":
        return cls(
            tenant_id=str(value.get("tenant_id", "")),
            user_id=str(value.get("user_id", "")),
            session_id=str(value.get("session_id", "")),
            request_id=str(value.get("request_id", "")),
            status=str(value.get("status", "")),
            limit=int(value.get("limit") or 100),
        )


@dataclass(frozen=True)
class RunWorkflowGraphRequest:
    """Run a persisted canvas graph — task nodes execute sequentially in
    edge order as delegated child runs (C+ semantics)."""

    tenant_id: str
    user_id: str
    session_id: str
    request_id: str
    graph_id: str
    parent_run_id: str = ""

    def __post_init__(self) -> None:
        _required_identifier(self.tenant_id, "tenant_id")
        _required_identifier(self.user_id, "user_id")
        _required_identifier(self.session_id, "session_id")
        _required_identifier(self.request_id, "request_id")
        _required_identifier(self.graph_id, "graph_id")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RunWorkflowGraphRequest":
        return cls(
            tenant_id=str(value.get("tenant_id", "")),
            user_id=str(value.get("user_id", "")),
            session_id=str(value.get("session_id", "")),
            request_id=str(value.get("request_id", "")),
            graph_id=str(value.get("graph_id", "")),
            parent_run_id=str(value.get("parent_run_id", "")),
        )


@dataclass(frozen=True)
class GraphRunStepSummary:
    node_id: str
    task_id: str
    run_id: str
    session_id: str

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "node_id": self.node_id,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "session_id": self.session_id,
        }


@dataclass(frozen=True)
class HandoffSummary:
    """Public delegation record: source run -> target run/task."""

    handoff_id: str
    source_run_id: str
    source_task_id: str
    target_task_id: str
    target_run_id: str
    target_planet: str
    context_refs: tuple[str, ...]
    approval_state: str
    created_at: str

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "handoff_id": self.handoff_id,
            "source_run_id": self.source_run_id,
            "source_task_id": self.source_task_id,
            "target_task_id": self.target_task_id,
            "target_run_id": self.target_run_id,
            "target_planet": self.target_planet,
            "context_refs": list(self.context_refs),
            "approval_state": self.approval_state,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class ListHandoffsRequest:
    """Identity-complete handoff query for one session."""

    tenant_id: str
    user_id: str
    session_id: str
    request_id: str
    limit: int = 500

    def __post_init__(self) -> None:
        _required_identifier(self.tenant_id, "tenant_id")
        _required_identifier(self.user_id, "user_id")
        _required_identifier(self.session_id, "session_id")
        _required_identifier(self.request_id, "request_id")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ListHandoffsRequest":
        return cls(
            tenant_id=str(value.get("tenant_id", "")),
            user_id=str(value.get("user_id", "")),
            session_id=str(value.get("session_id", "")),
            request_id=str(value.get("request_id", "")),
            limit=int(value.get("limit") or 500),
        )


@dataclass(frozen=True)
class ApprovalSummary:
    """Public approval card: request context plus the decision state."""

    approval_id: str
    run_id: str
    action: str
    scope: str
    params: Mapping[str, JSONValue]
    risk_level: str
    status: str
    decided_by: str
    decided_at: str
    created_at: str

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "approval_id": self.approval_id,
            "run_id": self.run_id,
            "action": self.action,
            "scope": self.scope,
            "params": _thaw_json(self.params),
            "risk_level": self.risk_level,
            "status": self.status,
            "decided_by": self.decided_by,
            "decided_at": self.decided_at,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class DecideApprovalRequest:
    """External decision for one pending approval."""

    tenant_id: str
    user_id: str
    session_id: str
    run_id: str
    approval_id: str
    decision: str  # "approve" | "deny"
    request_id: str
    decided_by: str = ""

    def __post_init__(self) -> None:
        _required_identifier(self.tenant_id, "tenant_id")
        _required_identifier(self.user_id, "user_id")
        _required_identifier(self.session_id, "session_id")
        _required_identifier(self.run_id, "run_id")
        _required_identifier(self.approval_id, "approval_id")
        _required_identifier(self.request_id, "request_id")
        if self.decision not in ("approve", "deny"):
            raise ValueError("decision must be 'approve' or 'deny'")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DecideApprovalRequest":
        return cls(
            tenant_id=str(value.get("tenant_id", "")),
            user_id=str(value.get("user_id", "")),
            session_id=str(value.get("session_id", "")),
            run_id=str(value.get("run_id", "")),
            approval_id=str(value.get("approval_id", "")),
            decision=str(value.get("decision", "")).lower(),
            request_id=str(value.get("request_id", "")),
            decided_by=str(value.get("decided_by", "")),
        )


@dataclass(frozen=True)
class ListApprovalsRequest:
    """Identity-complete approval query, run-scoped or session-wide."""

    tenant_id: str
    user_id: str
    session_id: str
    request_id: str
    run_id: str = ""
    status: str = ""
    limit: int = 200

    def __post_init__(self) -> None:
        _required_identifier(self.tenant_id, "tenant_id")
        _required_identifier(self.user_id, "user_id")
        _required_identifier(self.session_id, "session_id")
        _required_identifier(self.request_id, "request_id")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ListApprovalsRequest":
        return cls(
            tenant_id=str(value.get("tenant_id", "")),
            user_id=str(value.get("user_id", "")),
            session_id=str(value.get("session_id", "")),
            request_id=str(value.get("request_id", "")),
            run_id=str(value.get("run_id", "")),
            status=str(value.get("status", "")),
            limit=int(value.get("limit") or 200),
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
    run_id: str | None = None
    display_name: str | None = None
    size: int | None = None
    producer_workflow_id: str | None = None
    producer_stage_id: str | None = None
    created_revision: int = 0
    created_at: str = ""

    def __post_init__(self) -> None:
        _required_identifier(self.artifact_id, "artifact_id")
        _required_identifier(self.artifact_type, "artifact_type")
        if not isinstance(self.digest, str) or not isinstance(self.reference, str):
            raise ValueError("artifact digest/reference must be strings")
        _optional_identifier(self.run_id, "run_id")
        _optional_identifier(self.display_name, "display_name")
        if self.size is not None:
            _nonnegative_integer(self.size, "artifact size")
        _optional_identifier(self.producer_workflow_id, "producer_workflow_id")
        _optional_identifier(self.producer_stage_id, "producer_stage_id")
        _nonnegative_integer(self.created_revision, "created_revision")

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "artifact_id": self.artifact_id,
            "artifact_type": self.artifact_type,
            "digest": self.digest,
            "reference": self.reference,
            "exists": self.exists,
            "verified": self.verified,
            "run_id": self.run_id,
            "display_name": self.display_name,
            "size": self.size,
            "producer_workflow_id": self.producer_workflow_id,
            "producer_stage_id": self.producer_stage_id,
            "created_revision": self.created_revision,
            "created_at": self.created_at,
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
            run_id=(None if value.get("run_id") is None else str(value.get("run_id"))),
            display_name=(
                None if value.get("display_name") is None else str(value.get("display_name"))
            ),
            size=(
                None
                if value.get("size") is None
                else int(cast(str | int, value.get("size")))
            ),
            producer_workflow_id=(
                None
                if value.get("producer_workflow_id") is None
                else str(value.get("producer_workflow_id"))
            ),
            producer_stage_id=(
                None
                if value.get("producer_stage_id") is None
                else str(value.get("producer_stage_id"))
            ),
            created_revision=int(value.get("created_revision", 0)),
            created_at=str(value.get("created_at", "")),
        )


ArtifactView = ArtifactSummary


@dataclass(frozen=True)
class ReadArtifactRequest:
    """Read one artifact's workspace file through the guarded workspace path."""

    tenant_id: str
    user_id: str
    session_id: str
    request_id: str
    run_id: str
    artifact_id: str

    def __post_init__(self) -> None:
        _validate_identity(
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            session_id=self.session_id,
            run_id=self.run_id,
            request_id=self.request_id,
        )
        _required_identifier(self.artifact_id, "artifact_id")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ReadArtifactRequest":
        return cls(
            tenant_id=str(value.get("tenant_id", "")),
            user_id=str(value.get("user_id", "")),
            session_id=str(value.get("session_id", "")),
            request_id=str(value.get("request_id", "")),
            run_id=str(value.get("run_id", "")),
            artifact_id=str(value.get("artifact_id", "")),
        )


@dataclass(frozen=True)
class ArtifactContent:
    """Artifact file text; ``stale`` means the workspace file's digest no
    longer matches the committed artifact digest."""

    artifact_id: str
    reference: str
    text: str
    stale: bool

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "artifact_id": self.artifact_id,
            "reference": self.reference,
            "text": self.text,
            "stale": self.stale,
        }


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
    task_id: str = ""
    status: RunStatus = RunStatus.CREATED
    revision: int = 0
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        _required_identifier(self.tenant_id, "tenant_id")
        _required_identifier(self.session_id, "session_id")
        _required_identifier(self.run_id, "run_id")
        _required_identifier(self.request_id, "request_id")
        object.__setattr__(self, "status", RunStatus(self.status))
        _nonnegative_integer(self.revision, "revision")
        if not isinstance(self.created_at, str) or not self.created_at.strip():
            raise ValueError("created_at must be a non-empty string")
        if not isinstance(self.updated_at, str) or not self.updated_at.strip():
            object.__setattr__(self, "updated_at", self.created_at)

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "tenant_id": self.tenant_id,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "request_id": self.request_id,
            "task_id": self.task_id,
            "status": self.status.value,
            "revision": self.revision,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RunHandle":
        return cls(
            tenant_id=str(value.get("tenant_id", "")),
            session_id=str(value.get("session_id", "")),
            run_id=str(value.get("run_id", "")),
            request_id=str(value.get("request_id", "")),
            task_id=str(value.get("task_id", "")),
            status=RunStatus(str(value.get("status", ""))),
            revision=int(value.get("revision", 0)),
            created_at=str(value.get("created_at", "")),
            updated_at=str(value.get("updated_at", "")),
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
    request_id: str
    created_at: str
    updated_at: str
    completed_workflow_ids: tuple[str, ...] = ()
    pending_workflow_ids: tuple[str, ...] = ()
    artifacts: tuple[ArtifactSummary, ...] = ()
    verifier_summary: Mapping[str, JSONValue] | None = None
    resume_summary: ResumeSummary | None = None
    failure_summary: FailureSummary | None = None
    output: RunOutput | None = None
    revision: int = 0
    task_id: str = ""

    def __post_init__(self) -> None:
        _required_identifier(self.tenant_id, "tenant_id")
        _required_identifier(self.run_id, "run_id")
        _required_identifier(self.session_id, "session_id")
        _required_identifier(self.request_id, "request_id")
        object.__setattr__(self, "status", RunStatus(self.status))
        if not isinstance(self.request_text, str):
            raise ValueError("request_text must be a string")
        if not isinstance(self.created_at, str) or not self.created_at.strip():
            raise ValueError("created_at must be a non-empty string")
        if not isinstance(self.updated_at, str) or not self.updated_at.strip():
            raise ValueError("updated_at must be a non-empty string")
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
        if self.failure_summary is not None and not isinstance(self.failure_summary, FailureSummary):
            raise TypeError("failure_summary must be a FailureSummary or None")
        if self.output is not None and not isinstance(self.output, RunOutput):
            raise TypeError("output must be a RunOutput or None")
        if self.verifier_summary is not None:
            object.__setattr__(
                self,
                "verifier_summary",
                _freeze_object(self.verifier_summary, "verifier_summary"),
            )
        _nonnegative_integer(self.revision, "revision")

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "tenant_id": self.tenant_id,
            "run_id": self.run_id,
            "session_id": self.session_id,
            "status": self.status.value,
            "request_text": self.request_text,
            "active_workflow_id": self.active_workflow_id,
            "request_id": self.request_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "completed_workflow_ids": list(self.completed_workflow_ids),
            "pending_workflow_ids": list(self.pending_workflow_ids),
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "verifier_summary": (
                None if self.verifier_summary is None else _thaw_json(self.verifier_summary)
            ),
            "resume_summary": (
                self.resume_summary.to_dict() if self.resume_summary else None
            ),
            "failure_summary": (
                self.failure_summary.to_dict() if self.failure_summary else None
            ),
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
            request_id=str(value.get("request_id", "")),
            created_at=str(value.get("created_at", "")),
            updated_at=str(value.get("updated_at", "")),
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
            verifier_summary=(
                None
                if value.get("verifier_summary") is None
                else cast(Mapping[str, JSONValue], value.get("verifier_summary"))
            ),
            resume_summary=(
                None
                if value.get("resume_summary") is None
                else ResumeSummary.from_dict(
                    cast(Mapping[str, Any], value["resume_summary"])
                )
            ),
            failure_summary=(
                None
                if value.get("failure_summary") is None
                else FailureSummary.from_dict(
                    cast(Mapping[str, Any], value["failure_summary"])
                )
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
        try:
            object.__setattr__(self, "event_type", EventType(self.event_type))
        except ValueError:
            # Internal progress events (task.delegated, transcript_compacted,
            # …) are arbitrary names; the wire contract accepts them as-is.
            object.__setattr__(self, "event_type", str(self.event_type))
        _positive_integer(self.sequence_number, "sequence_number")
        _nonnegative_integer(self.run_revision, "run_revision")
        if not isinstance(self.timestamp, str) or not self.timestamp.strip():
            raise ValueError("timestamp must be a non-empty string")
        object.__setattr__(self, "payload", _freeze_object(self.payload or {}, "payload"))

    @property
    def is_terminal(self) -> bool:
        if isinstance(self.event_type, EventType):
            return self.event_type.is_terminal
        return str(self.event_type) in {
            "run_completed", "run_failed", "run_blocked",
            "run_cancelled", "run_timed_out",
        }

    @property
    def public_type(self) -> str:
        """Versioned public event contract name, e.g. run.started.v1.

        Internal snake_case names stay as-is for durability; the wire form is
        the dotted versioned contract the plan specifies."""
        raw = (
            self.event_type.value
            if isinstance(self.event_type, EventType)
            else str(self.event_type)
        )
        return f"{raw.replace('_', '.')}.v1"

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
            "event_type": (
                self.event_type.value
                if isinstance(self.event_type, EventType)
                else str(self.event_type)
            ),
            "public_type": self.public_type,
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
    "ExternalStateEvidence",
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


@dataclass(frozen=True)
class AssignSessionWorkspaceRequest:
    tenant_id: str
    user_id: str
    session_id: str
    request_id: str
    workspace_id: str  # "" clears the assignment

    def __post_init__(self) -> None:
        _required_identifier(self.tenant_id, "tenant_id")
        _required_identifier(self.user_id, "user_id")
        _required_identifier(self.session_id, "session_id")
        _required_identifier(self.request_id, "request_id")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AssignSessionWorkspaceRequest":
        return cls(
            tenant_id=str(value.get("tenant_id", "")),
            user_id=str(value.get("user_id", "")),
            session_id=str(value.get("session_id", "")),
            request_id=str(value.get("request_id", "")),
            workspace_id=str(value.get("workspace_id", "")),
        )


@dataclass(frozen=True)
class DeleteWorkspaceRequest:
    tenant_id: str
    user_id: str
    session_id: str
    request_id: str
    workspace_id: str

    def __post_init__(self) -> None:
        _required_identifier(self.tenant_id, "tenant_id")
        _required_identifier(self.user_id, "user_id")
        _required_identifier(self.session_id, "session_id")
        _required_identifier(self.request_id, "request_id")
        _required_identifier(self.workspace_id, "workspace_id")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DeleteWorkspaceRequest":
        return cls(
            tenant_id=str(value.get("tenant_id", "")),
            user_id=str(value.get("user_id", "")),
            session_id=str(value.get("session_id", "")),
            request_id=str(value.get("request_id", "")),
            workspace_id=str(value.get("workspace_id", "")),
        )
