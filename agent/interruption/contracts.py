"""Frozen v2.3D-1 cancellation and timeout contracts.

The types in this module are data and policy inputs only.  They do not cancel
an asyncio Task, call a Provider, interrupt a Tool, or mutate durable state.
Those production integrations belong to v2.3D-2 and v2.3D-3.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, cast


JSONValue = Any


def _required(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _nonnegative(value: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value


def _freeze_json(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("interruption JSON object keys must be strings")
            frozen[key] = _freeze_json(item)
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    raise TypeError(
        "interruption contract accepts JSON values only; "
        f"got {type(value).__name__}"
    )


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _canonical_digest(value: Mapping[str, Any]) -> str:
    payload = json.dumps(
        _thaw_json(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class InterruptionReason(str, Enum):
    USER_CANCEL = "USER_CANCEL"
    RUN_TIMEOUT = "RUN_TIMEOUT"
    STAGE_TIMEOUT = "STAGE_TIMEOUT"
    TOOL_TIMEOUT = "TOOL_TIMEOUT"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    SERVICE_SHUTDOWN = "SERVICE_SHUTDOWN"


class InterruptionAction(str, Enum):
    CANCEL_AT_SAFE_BOUNDARY = "CANCEL_AT_SAFE_BOUNDARY"
    TIME_OUT_AT_SAFE_BOUNDARY = "TIME_OUT_AT_SAFE_BOUNDARY"
    SUSPEND_AT_SAFE_BOUNDARY = "SUSPEND_AT_SAFE_BOUNDARY"
    DELEGATE_TO_DECISION = "DELEGATE_TO_DECISION"


class InterruptionPhase(str, Enum):
    REQUESTED = "REQUESTED"
    OBSERVED = "OBSERVED"
    CANCELLING = "CANCELLING"
    FINALIZED = "FINALIZED"


class InterruptionResultStatus(str, Enum):
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"
    SUSPENDED = "SUSPENDED"


class CancellationSafetyClass(str, Enum):
    INTERRUPTIBLE = "INTERRUPTIBLE"
    BOUNDARY_ONLY = "BOUNDARY_ONLY"
    NON_CANCELLABLE_ONCE_COMMITTED = "NON_CANCELLABLE_ONCE_COMMITTED"


class SafeCancellationBoundary(str, Enum):
    BEFORE_PLANNER = "BEFORE_PLANNER"
    AFTER_PLANNER = "AFTER_PLANNER"
    BEFORE_TOOL = "BEFORE_TOOL"
    AFTER_TOOL = "AFTER_TOOL"
    BEFORE_WORKFLOW_ACTIVATION = "BEFORE_WORKFLOW_ACTIVATION"
    AFTER_FINALIZATION_BUNDLE = "AFTER_FINALIZATION_BUNDLE"
    DURING_INTERRUPTIBLE_WAIT = "DURING_INTERRUPTIBLE_WAIT"


class AtomicRegion(str, Enum):
    SQLITE_TRANSACTION = "SQLITE_TRANSACTION"
    ARTIFACT_DIGEST_COMMIT = "ARTIFACT_DIGEST_COMMIT"
    IDEMPOTENCY_FINALIZATION = "IDEMPOTENCY_FINALIZATION"
    FILESYSTEM_ATOMIC_REPLACE = "FILESYSTEM_ATOMIC_REPLACE"


@dataclass(frozen=True)
class CancelRunRequest:
    """Identity-complete public request; implementation is deferred to D2."""

    tenant_id: str
    user_id: str
    session_id: str
    run_id: str
    request_id: str
    requested_by: str
    reason: InterruptionReason = InterruptionReason.USER_CANCEL

    def __post_init__(self) -> None:
        for label in (
            "tenant_id",
            "user_id",
            "session_id",
            "run_id",
            "request_id",
            "requested_by",
        ):
            _required(str(getattr(self, label)), label)
        object.__setattr__(self, "reason", InterruptionReason(self.reason))
        if self.reason is not InterruptionReason.USER_CANCEL:
            raise ValueError("public CancelRunRequest only accepts USER_CANCEL")

    def to_dict(self) -> dict[str, str]:
        return {
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "request_id": self.request_id,
            "requested_by": self.requested_by,
            "reason": self.reason.value,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CancelRunRequest":
        return cls(
            tenant_id=str(value.get("tenant_id", "")),
            user_id=str(value.get("user_id", "")),
            session_id=str(value.get("session_id", "")),
            run_id=str(value.get("run_id", "")),
            request_id=str(value.get("request_id", "")),
            requested_by=str(value.get("requested_by", "")),
            reason=InterruptionReason(str(value.get("reason", ""))),
        )

    @property
    def request_digest(self) -> str:
        return _canonical_digest(self.to_dict())


@dataclass(frozen=True)
class CancellationIntent:
    """Durable interruption fact written before cancellation is acted on."""

    tenant_id: str
    user_id: str
    session_id: str
    run_id: str
    request_id: str
    requested_at: str
    requested_by: str
    reason: InterruptionReason
    revision: int
    phase: InterruptionPhase = InterruptionPhase.REQUESTED
    details: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for label in (
            "tenant_id",
            "user_id",
            "session_id",
            "run_id",
            "request_id",
            "requested_at",
            "requested_by",
        ):
            _required(str(getattr(self, label)), label)
        _nonnegative(self.revision, "revision")
        object.__setattr__(self, "reason", InterruptionReason(self.reason))
        object.__setattr__(self, "phase", InterruptionPhase(self.phase))
        frozen = _freeze_json(self.details or {})
        object.__setattr__(self, "details", cast(Mapping[str, JSONValue], frozen))

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "request_id": self.request_id,
            "requested_at": self.requested_at,
            "requested_by": self.requested_by,
            "reason": self.reason.value,
            "revision": self.revision,
            "phase": self.phase.value,
            "details": _thaw_json(self.details),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CancellationIntent":
        return cls(
            tenant_id=str(value.get("tenant_id", "")),
            user_id=str(value.get("user_id", "")),
            session_id=str(value.get("session_id", "")),
            run_id=str(value.get("run_id", "")),
            request_id=str(value.get("request_id", "")),
            requested_at=str(value.get("requested_at", "")),
            requested_by=str(value.get("requested_by", "")),
            reason=InterruptionReason(str(value.get("reason", ""))),
            revision=int(value.get("revision", 0)),
            phase=InterruptionPhase(str(value.get("phase", ""))),
            details=cast(Mapping[str, JSONValue], value.get("details", {}) or {}),
        )

    @property
    def intent_digest(self) -> str:
        return _canonical_digest(self.to_dict())


# Generic name used by timeout/shutdown code without creating another record.
InterruptionIntent = CancellationIntent


@dataclass(frozen=True)
class InterruptionPolicy:
    reason: InterruptionReason
    action: InterruptionAction
    resulting_status: InterruptionResultStatus | None
    resume_allowed: bool
    decision_owned: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "reason", InterruptionReason(self.reason))
        object.__setattr__(self, "action", InterruptionAction(self.action))
        if self.resulting_status is not None:
            object.__setattr__(
                self,
                "resulting_status",
                InterruptionResultStatus(self.resulting_status),
            )
        if self.action is InterruptionAction.DELEGATE_TO_DECISION:
            if not self.decision_owned or self.resulting_status is not None:
                raise ValueError("delegated timeout policy is Decision-owned and non-terminal")
        elif self.decision_owned:
            raise ValueError("only delegated timeout policy may be Decision-owned")

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "reason": self.reason.value,
            "action": self.action.value,
            "resulting_status": (
                None if self.resulting_status is None else self.resulting_status.value
            ),
            "resume_allowed": self.resume_allowed,
            "decision_owned": self.decision_owned,
        }


__all__ = [
    "AtomicRegion",
    "CancellationIntent",
    "CancellationSafetyClass",
    "CancelRunRequest",
    "InterruptionAction",
    "InterruptionIntent",
    "InterruptionPhase",
    "InterruptionPolicy",
    "InterruptionReason",
    "InterruptionResultStatus",
    "SafeCancellationBoundary",
]
