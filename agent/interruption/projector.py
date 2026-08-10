"""Public-safe projection of durable interruption facts."""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import InterruptionPhase, InterruptionReason
from .store import DurableInterruptionRecord


@dataclass(frozen=True)
class InterruptionProjection:
    request_id: str
    reason: InterruptionReason
    phase: InterruptionPhase
    run_status: str
    revision: int


class InterruptionProjector:
    """Project only stable cancellation fields; never leak Store rows."""

    @staticmethod
    def project(
        record: DurableInterruptionRecord,
        *,
        run_status: str,
    ) -> InterruptionProjection:
        return InterruptionProjection(
            request_id=record.intent.request_id,
            reason=record.intent.reason,
            phase=record.intent.phase,
            run_status=str(run_status).upper(),
            revision=record.updated_revision,
        )


__all__ = ["InterruptionProjection", "InterruptionProjector"]
