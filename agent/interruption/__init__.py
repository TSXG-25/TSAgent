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

__all__ = [
    "ALLOWED_PHASE_TRANSITIONS",
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
    "InvalidInterruptionTransition",
    "SafeCancellationBoundary",
    "allowed_phase_transition",
    "can_observe_interruption",
    "interruption_policy",
    "interruption_policy_contract",
    "phase_lifecycle_contract",
    "validate_phase_transition",
]
