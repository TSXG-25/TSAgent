"""Durable cancellation Store contracts for v2.3D-2.

The contracts in this module describe persisted facts only.  They deliberately
do not expose asyncio tasks or execution-layer cancellation hooks.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from .contracts import CancellationIntent, InterruptionPhase


class InterruptionFailurePoint(str, Enum):
    """Test-only rollback points inside interruption transactions."""

    AFTER_INTENT_INSERT = "AFTER_INTENT_INSERT"
    AFTER_REVISION_INSERT = "AFTER_REVISION_INSERT"
    AFTER_EVENT_APPEND = "AFTER_EVENT_APPEND"
    AFTER_PHASE_UPDATE = "AFTER_PHASE_UPDATE"
    AFTER_HEAD_UPDATE = "AFTER_HEAD_UPDATE"
    BEFORE_COMMIT = "BEFORE_COMMIT"


@dataclass(frozen=True)
class DurableInterruptionRecord:
    """One immutable view of the current durable interruption intent."""

    intent: CancellationIntent
    request_digest: str
    created_revision: int
    updated_revision: int
    idempotent: bool = False

    def __post_init__(self) -> None:
        if not self.request_digest:
            raise ValueError("request_digest must be non-empty")
        if self.created_revision < 1:
            raise ValueError("created_revision must be positive")
        if self.updated_revision < self.created_revision:
            raise ValueError("updated_revision must not precede created_revision")


class CancellationStore(Protocol):
    """Minimal persistence port consumed by ``CancellationCoordinator``."""

    def request_interruption(
        self,
        intent: CancellationIntent,
        *,
        request_digest: str,
        failure_point: InterruptionFailurePoint | None = None,
    ) -> DurableInterruptionRecord: ...

    def get_interruption(
        self,
        tenant_id: str,
        run_id: str,
        *,
        session_id: str | None = None,
    ) -> DurableInterruptionRecord | None: ...

    def advance_interruption_phase(
        self,
        tenant_id: str,
        session_id: str,
        run_id: str,
        *,
        request_id: str,
        target_phase: InterruptionPhase,
        writer_id: str,
        fence_token: int,
        failure_point: InterruptionFailurePoint | None = None,
    ) -> DurableInterruptionRecord: ...

    def finalize_interruption(
        self,
        tenant_id: str,
        session_id: str,
        run_id: str,
        *,
        request_id: str,
        writer_id: str,
        fence_token: int,
        failure_point: InterruptionFailurePoint | None = None,
    ) -> DurableInterruptionRecord: ...


__all__ = [
    "CancellationStore",
    "DurableInterruptionRecord",
    "InterruptionFailurePoint",
]
