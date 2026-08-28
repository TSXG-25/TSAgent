"""Public interruption contracts with lazy submodule loading.

The local transport only needs the request DTO during process bootstrap.  Do
not import the Coordinator, durable Store, or cancellation View until a
caller actually asks for one of those public symbols.
"""

from __future__ import annotations

import importlib
from typing import Any


_EXPORTS: dict[str, tuple[str, str]] = {
    "AtomicRegion": ("contracts", "AtomicRegion"),
    "CancellationIntent": ("contracts", "CancellationIntent"),
    "CancellationSafetyClass": ("contracts", "CancellationSafetyClass"),
    "CancelRunRequest": ("contracts", "CancelRunRequest"),
    "InterruptionAction": ("contracts", "InterruptionAction"),
    "InterruptionIntent": ("contracts", "InterruptionIntent"),
    "InterruptionPhase": ("contracts", "InterruptionPhase"),
    "InterruptionPolicy": ("contracts", "InterruptionPolicy"),
    "InterruptionReason": ("contracts", "InterruptionReason"),
    "InterruptionResultStatus": ("contracts", "InterruptionResultStatus"),
    "SafeCancellationBoundary": ("contracts", "SafeCancellationBoundary"),
    "CancellationCoordinator": ("coordinator", "CancellationCoordinator"),
    "ALLOWED_PHASE_TRANSITIONS": ("lifecycle", "ALLOWED_PHASE_TRANSITIONS"),
    "InvalidInterruptionTransition": ("lifecycle", "InvalidInterruptionTransition"),
    "allowed_phase_transition": ("lifecycle", "allowed_phase_transition"),
    "phase_lifecycle_contract": ("lifecycle", "phase_lifecycle_contract"),
    "validate_phase_transition": ("lifecycle", "validate_phase_transition"),
    "can_observe_interruption": ("policy", "can_observe_interruption"),
    "interruption_policy": ("policy", "interruption_policy"),
    "interruption_policy_contract": ("policy", "interruption_policy_contract"),
    "InterruptionProjection": ("projector", "InterruptionProjection"),
    "InterruptionProjector": ("projector", "InterruptionProjector"),
    "CancellationStore": ("store", "CancellationStore"),
    "DurableInterruptionRecord": ("store", "DurableInterruptionRecord"),
    "InterruptionFailurePoint": ("store", "InterruptionFailurePoint"),
    "CancellationView": ("view", "CancellationView"),
    "InterruptionObservation": ("view", "InterruptionObservation"),
    "RunInterruptionRequested": ("view", "RunInterruptionRequested"),
    "await_interruptibly": ("view", "await_interruptibly"),
    "cancellation_scope": ("view", "cancellation_scope"),
    "current_cancellation_view": ("view", "current_cancellation_view"),
    "tool_cancellation_safety": ("safety", "tool_cancellation_safety"),
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    module_name, attribute = target
    value = getattr(importlib.import_module(f".{module_name}", __name__), attribute)
    globals()[name] = value
    return value
