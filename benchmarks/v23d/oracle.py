"""Pure v2.3D-1 interruption oracle; no Runtime, Provider, Store, or I/O."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.interruption import (
    AtomicRegion,
    CancellationIntent,
    InterruptionAction,
    InterruptionResultStatus,
    SafeCancellationBoundary,
    can_observe_interruption,
    interruption_policy,
)

from .cases import ExpectedOutcome, InterruptionContractCase, Probe


@dataclass(frozen=True)
class OracleDecision:
    case_id: str
    outcome: ExpectedOutcome
    action: InterruptionAction | None
    resulting_status: InterruptionResultStatus | None
    resume_allowed: bool
    evidence: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "outcome": self.outcome.value,
            "action": None if self.action is None else self.action.value,
            "resulting_status": (
                None if self.resulting_status is None else self.resulting_status.value
            ),
            "resume_allowed": self.resume_allowed,
            "evidence": self.evidence,
        }


def _intent(case: InterruptionContractCase) -> CancellationIntent:
    return CancellationIntent(
        tenant_id="tenant-1",
        user_id="user-1",
        session_id="session-1",
        run_id="run-1",
        request_id=f"request-{case.id.lower()}",
        requested_at="2026-08-10T00:00:00Z",
        requested_by="user-1",
        reason=case.reason,
        revision=7,
        details={"case_id": case.id},
    )


def _policy_decision(
    case: InterruptionContractCase,
    *,
    evidence: str,
) -> OracleDecision:
    policy = interruption_policy(case.reason)
    if policy.resulting_status is InterruptionResultStatus.CANCELLED:
        outcome = ExpectedOutcome.CANCELLED
    elif policy.resulting_status is InterruptionResultStatus.TIMED_OUT:
        outcome = ExpectedOutcome.TIMED_OUT
    elif policy.action is InterruptionAction.DELEGATE_TO_DECISION:
        outcome = ExpectedOutcome.DELEGATED
    else:
        raise ValueError(f"{case.id}: Dataset does not score {policy.action.value}")
    return OracleDecision(
        case_id=case.id,
        outcome=outcome,
        action=policy.action,
        resulting_status=policy.resulting_status,
        resume_allowed=policy.resume_allowed,
        evidence=evidence,
    )


def evaluate(case: InterruptionContractCase) -> OracleDecision:
    """Evaluate the frozen fixture semantics without executing cancellation."""

    probe = case.probe
    if probe is Probe.CANCEL_DURING_PROVIDER_WAIT:
        if not can_observe_interruption(
            SafeCancellationBoundary.DURING_INTERRUPTIBLE_WAIT,
            case.safety_class,
        ):
            raise AssertionError("interruptible Provider wait must be observable")
        return _policy_decision(case, evidence="interruptible wait boundary")
    if probe is Probe.CANCEL_BEFORE_FILESYSTEM_WRITE:
        if not can_observe_interruption(
            SafeCancellationBoundary.BEFORE_TOOL,
            case.safety_class,
        ):
            raise AssertionError("before-tool boundary must be observable")
        return _policy_decision(case, evidence="before filesystem effect")
    if probe is Probe.CANCEL_AFTER_EFFECT_COMMIT:
        if not can_observe_interruption(
            SafeCancellationBoundary.AFTER_TOOL,
            case.safety_class,
        ):
            raise AssertionError("post-commit boundary must be observable")
        return _policy_decision(case, evidence="committed effect preserved")
    if probe is Probe.CANCEL_DURING_FINALIZATION:
        inside = can_observe_interruption(
            SafeCancellationBoundary.AFTER_FINALIZATION_BUNDLE,
            case.safety_class,
            atomic_region=AtomicRegion.SQLITE_TRANSACTION,
        )
        after = can_observe_interruption(
            SafeCancellationBoundary.AFTER_FINALIZATION_BUNDLE,
            case.safety_class,
        )
        if inside or not after:
            raise AssertionError("Finalization Bundle must be atomic and boundary-observed")
        return _policy_decision(case, evidence="observed after Finalization Bundle")
    if probe is Probe.DUPLICATE_CANCEL_REQUEST:
        first = _intent(case)
        retry = CancellationIntent.from_dict(first.to_dict())
        if first.intent_digest != retry.intent_digest:
            raise AssertionError("same cancel request must have one intent digest")
        return OracleDecision(
            case.id, ExpectedOutcome.IDEMPOTENT, None, None, False,
            "same request and intent digest",
        )
    if probe is Probe.CANCEL_COMPLETED_RUN:
        return OracleDecision(
            case.id, ExpectedOutcome.REJECTED, None, None, False,
            "COMPLETED is terminal",
        )
    if probe is Probe.CANCEL_ALREADY_CANCELLED:
        return OracleDecision(
            case.id, ExpectedOutcome.IDEMPOTENT, None,
            InterruptionResultStatus.CANCELLED, False,
            "existing CANCELLED fact is returned",
        )
    if probe is Probe.PROCESS_DIES_AFTER_INTENT:
        intent = _intent(case)
        restored = CancellationIntent.from_dict(intent.to_dict())
        if restored.intent_digest != intent.intent_digest:
            raise AssertionError("durable intent must rehydrate identically")
        return _policy_decision(case, evidence="intent survives process restart")
    if probe is Probe.NEW_WORKER_OBSERVES_CANCEL:
        policy = interruption_policy(case.reason)
        if policy.resume_allowed:
            raise AssertionError("USER_CANCEL cannot auto-resume")
        return _policy_decision(case, evidence="new worker does not resume")
    if probe is Probe.RUN_TIMEOUT:
        return _policy_decision(case, evidence="Run timeout is terminal TIMED_OUT")
    if probe is Probe.TOOL_TIMEOUT_DELEGATED:
        return _policy_decision(case, evidence="Tool timeout is Decision-owned")
    if probe is Probe.STALE_WRITER_AFTER_CANCEL:
        return OracleDecision(
            case.id, ExpectedOutcome.REJECTED, None, None, False,
            "stale fence is rejected",
        )
    if probe in {
        Probe.CANCEL_BEFORE_FIRST_TOOL,
        Probe.CANCEL_MULTI_WORKFLOW,
        Probe.CANCEL_WITH_CLIENT_DISCONNECT,
        Probe.EXTERNAL_COMMITTED_EFFECT,
    }:
        return _policy_decision(case, evidence=probe.value.lower())
    raise AssertionError(f"unhandled interruption probe: {probe.value}")


__all__ = ["OracleDecision", "evaluate"]
