"""Pure interruption policy and safe-boundary matrix for ADR-0023."""

from __future__ import annotations

from .contracts import (
    AtomicRegion,
    CancellationSafetyClass,
    InterruptionAction,
    InterruptionPolicy,
    InterruptionReason,
    InterruptionResultStatus,
    SafeCancellationBoundary,
)


_POLICIES = {
    InterruptionReason.USER_CANCEL: InterruptionPolicy(
        reason=InterruptionReason.USER_CANCEL,
        action=InterruptionAction.CANCEL_AT_SAFE_BOUNDARY,
        resulting_status=InterruptionResultStatus.CANCELLED,
        resume_allowed=False,
        decision_owned=False,
    ),
    InterruptionReason.RUN_TIMEOUT: InterruptionPolicy(
        reason=InterruptionReason.RUN_TIMEOUT,
        action=InterruptionAction.TIME_OUT_AT_SAFE_BOUNDARY,
        resulting_status=InterruptionResultStatus.TIMED_OUT,
        resume_allowed=False,
        decision_owned=False,
    ),
    InterruptionReason.SERVICE_SHUTDOWN: InterruptionPolicy(
        reason=InterruptionReason.SERVICE_SHUTDOWN,
        action=InterruptionAction.SUSPEND_AT_SAFE_BOUNDARY,
        resulting_status=InterruptionResultStatus.SUSPENDED,
        resume_allowed=True,
        decision_owned=False,
    ),
    InterruptionReason.STAGE_TIMEOUT: InterruptionPolicy(
        reason=InterruptionReason.STAGE_TIMEOUT,
        action=InterruptionAction.DELEGATE_TO_DECISION,
        resulting_status=None,
        resume_allowed=False,
        decision_owned=True,
    ),
    InterruptionReason.TOOL_TIMEOUT: InterruptionPolicy(
        reason=InterruptionReason.TOOL_TIMEOUT,
        action=InterruptionAction.DELEGATE_TO_DECISION,
        resulting_status=None,
        resume_allowed=False,
        decision_owned=True,
    ),
    InterruptionReason.PROVIDER_TIMEOUT: InterruptionPolicy(
        reason=InterruptionReason.PROVIDER_TIMEOUT,
        action=InterruptionAction.DELEGATE_TO_DECISION,
        resulting_status=None,
        resume_allowed=False,
        decision_owned=True,
    ),
}


def interruption_policy(reason: InterruptionReason | str) -> InterruptionPolicy:
    return _POLICIES[InterruptionReason(reason)]


def can_observe_interruption(
    boundary: SafeCancellationBoundary | str,
    safety_class: CancellationSafetyClass | str,
    *,
    atomic_region: AtomicRegion | str | None = None,
) -> bool:
    """Return whether an interruption may be acted on at this exact point.

    Any declared atomic region is fail-closed.  Inside a long-running wait,
    only an explicitly INTERRUPTIBLE operation can be interrupted.  All other
    enum values are already named safe boundaries before or after an operation.
    """

    point = SafeCancellationBoundary(boundary)
    safety = CancellationSafetyClass(safety_class)
    if atomic_region is not None:
        AtomicRegion(atomic_region)
        return False
    if point is SafeCancellationBoundary.DURING_INTERRUPTIBLE_WAIT:
        return safety is CancellationSafetyClass.INTERRUPTIBLE
    return True


def interruption_policy_contract() -> dict[str, dict[str, object]]:
    return {
        reason.value: policy.to_dict()
        for reason, policy in sorted(_POLICIES.items(), key=lambda item: item[0].value)
    }


__all__ = [
    "can_observe_interruption",
    "interruption_policy",
    "interruption_policy_contract",
]
