"""Durable interruption-intent lifecycle for ADR-0023."""

from __future__ import annotations

from collections.abc import Mapping

from .contracts import InterruptionPhase


ALLOWED_PHASE_TRANSITIONS: Mapping[
    InterruptionPhase, frozenset[InterruptionPhase]
] = {
    InterruptionPhase.REQUESTED: frozenset({InterruptionPhase.OBSERVED}),
    InterruptionPhase.OBSERVED: frozenset(
        {InterruptionPhase.CANCELLING, InterruptionPhase.FINALIZED}
    ),
    InterruptionPhase.CANCELLING: frozenset({InterruptionPhase.FINALIZED}),
    InterruptionPhase.FINALIZED: frozenset(),
}


class InvalidInterruptionTransition(ValueError):
    """Raised when a durable intent attempts an undeclared transition."""


def allowed_phase_transition(
    current: InterruptionPhase | str,
    target: InterruptionPhase | str,
) -> bool:
    source = InterruptionPhase(current)
    destination = InterruptionPhase(target)
    return destination in ALLOWED_PHASE_TRANSITIONS[source]


def validate_phase_transition(
    current: InterruptionPhase | str,
    target: InterruptionPhase | str,
) -> None:
    source = InterruptionPhase(current)
    destination = InterruptionPhase(target)
    if not allowed_phase_transition(source, destination):
        raise InvalidInterruptionTransition(
            f"invalid interruption transition: {source.value} -> {destination.value}"
        )


def phase_lifecycle_contract() -> dict[str, list[str]]:
    return {
        source.value: sorted(target.value for target in targets)
        for source, targets in ALLOWED_PHASE_TRANSITIONS.items()
    }


__all__ = [
    "ALLOWED_PHASE_TRANSITIONS",
    "InvalidInterruptionTransition",
    "allowed_phase_transition",
    "phase_lifecycle_contract",
    "validate_phase_transition",
]
