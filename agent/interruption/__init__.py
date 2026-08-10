"""Contract-only interruption model for v2.3D-1 (ADR-0023)."""

from .contracts import (
    AtomicRegion,
    CancellationIntent,
    CancellationSafetyClass,
    CancelRunRequest,
    InterruptionAction,
    InterruptionIntent,
    InterruptionPhase,
    InterruptionPolicy,
    InterruptionReason,
    InterruptionResultStatus,
    SafeCancellationBoundary,
)
from .coordinator import CancellationCoordinator
from .lifecycle import (
    ALLOWED_PHASE_TRANSITIONS,
    InvalidInterruptionTransition,
    allowed_phase_transition,
    phase_lifecycle_contract,
    validate_phase_transition,
)
from .policy import (
    can_observe_interruption,
    interruption_policy,
    interruption_policy_contract,
)
from .projector import InterruptionProjection, InterruptionProjector
from .store import (
    CancellationStore,
    DurableInterruptionRecord,
    InterruptionFailurePoint,
)
from .view import (
    CancellationView,
    InterruptionObservation,
    RunInterruptionRequested,
    await_interruptibly,
    cancellation_scope,
    current_cancellation_view,
)
from .safety import tool_cancellation_safety

__all__ = [
    "ALLOWED_PHASE_TRANSITIONS",
    "AtomicRegion",
    "CancellationIntent",
    "CancellationCoordinator",
    "CancellationSafetyClass",
    "CancellationStore",
    "CancellationView",
    "CancelRunRequest",
    "InterruptionAction",
    "InterruptionFailurePoint",
    "InterruptionIntent",
    "InterruptionObservation",
    "InterruptionPhase",
    "InterruptionPolicy",
    "InterruptionProjection",
    "InterruptionProjector",
    "InterruptionReason",
    "InterruptionResultStatus",
    "DurableInterruptionRecord",
    "InvalidInterruptionTransition",
    "SafeCancellationBoundary",
    "RunInterruptionRequested",
    "await_interruptibly",
    "allowed_phase_transition",
    "can_observe_interruption",
    "cancellation_scope",
    "current_cancellation_view",
    "interruption_policy",
    "interruption_policy_contract",
    "phase_lifecycle_contract",
    "tool_cancellation_safety",
    "validate_phase_transition",
]
