"""Read-only Runtime projection of durable interruption intent.

``CancellationView`` deliberately cannot create, advance, or finalize an
intent.  Runtime components use it only to decide whether a safe boundary may
start more work.  Durable lifecycle convergence remains owned by
``CancellationCoordinator`` at the Service launcher boundary.
"""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import (
    AtomicRegion,
    CancellationSafetyClass,
    SafeCancellationBoundary,
)
from .policy import can_observe_interruption
from .store import CancellationStore, DurableInterruptionRecord


@dataclass(frozen=True)
class InterruptionObservation:
    """One safe-boundary observation of a durable interruption intent."""

    record: DurableInterruptionRecord
    boundary: SafeCancellationBoundary
    safety_class: CancellationSafetyClass


class RunInterruptionRequested(RuntimeError):
    """Cooperative control signal raised only at a declared safe boundary."""

    def __init__(self, observation: InterruptionObservation) -> None:
        self.observation = observation
        intent = observation.record.intent
        super().__init__(
            f"Run interruption requested: {intent.reason.value} "
            f"at {observation.boundary.value}"
        )


class CancellationView:
    """Identity-scoped, read-only view over the durable interruption Store."""

    def __init__(
        self,
        store: CancellationStore,
        *,
        tenant_id: str,
        session_id: str,
        run_id: str,
    ) -> None:
        self._store = store
        self.tenant_id = str(tenant_id).strip()
        self.session_id = str(session_id).strip()
        self.run_id = str(run_id).strip()
        if not self.tenant_id or not self.session_id or not self.run_id:
            raise ValueError("CancellationView identity must be complete")

    def current(self) -> DurableInterruptionRecord | None:
        """Return the current durable intent without mutating its lifecycle."""

        return self._store.get_interruption(
            self.tenant_id,
            self.run_id,
            session_id=self.session_id,
        )

    def observe_at(
        self,
        boundary: SafeCancellationBoundary | str,
        safety_class: CancellationSafetyClass | str,
        *,
        atomic_region: AtomicRegion | str | None = None,
    ) -> InterruptionObservation | None:
        """Project an intent only when the declared boundary is interruptible."""

        point = SafeCancellationBoundary(boundary)
        safety = CancellationSafetyClass(safety_class)
        if not can_observe_interruption(
            point,
            safety,
            atomic_region=atomic_region,
        ):
            return None
        record = self.current()
        if record is None:
            return None
        return InterruptionObservation(record, point, safety)

    def raise_if_requested(
        self,
        boundary: SafeCancellationBoundary | str,
        safety_class: CancellationSafetyClass | str,
        *,
        atomic_region: AtomicRegion | str | None = None,
    ) -> None:
        observation = self.observe_at(
            boundary,
            safety_class,
            atomic_region=atomic_region,
        )
        if observation is not None:
            raise RunInterruptionRequested(observation)


__all__ = [
    "CancellationView",
    "InterruptionObservation",
    "RunInterruptionRequested",
]
