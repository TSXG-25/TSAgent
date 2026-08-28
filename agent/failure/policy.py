"""The single production seam from structural failure to recovery policy.

Ordinary action failures are observations and must not enter this module.
Only structural failures are converted into a Reflection proposal and a
deterministic Decision.  The Runtime consumes the resulting directive rather
than importing either subsystem directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from agent.failure.contracts import Evidence, FailureEvent, SYMPTOM_MAP
from agent.failure.taxonomy import FailureFact, FailureKind


ASK = "ask"
FINISH = "finish"
RETRY = "retry"
SWITCH = "switch"


OBSERVE = "observe"
RECOVERY_ACTIONS = frozenset({RETRY, SWITCH, ASK, FINISH, OBSERVE})


@dataclass(frozen=True)
class RecoveryDirective:
    """Machine-readable recovery decision consumed by Runtime."""

    action: str
    reason: str
    failure: FailureFact
    diagnosis: str = ""
    correction: str = ""
    confidence: float = 0.0
    event_id: str = ""
    decision_id: str = ""

    def __post_init__(self) -> None:
        if self.action not in RECOVERY_ACTIONS:
            raise ValueError(f"unsupported recovery action: {self.action}")

    @property
    def terminal(self) -> bool:
        return self.action in {ASK, FINISH}

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "reason": self.reason,
            "failure": self.failure.to_dict(),
            "diagnosis": self.diagnosis,
            "correction": self.correction,
            "confidence": self.confidence,
            "event_id": self.event_id,
            "decision_id": self.decision_id,
        }


def _symptom_for(failure: FailureFact) -> str:
    code = failure.code.upper()
    if "TIMEOUT" in code:
        return "timeout"
    if code in {"PERMISSION_BOUNDARY", "EFFECT_SCOPE_VIOLATION", "UNSUPPORTED_CAPABILITY"}:
        return "contract_violation"
    if code in {"CONTRACT_VIOLATION", "RUNTIME_INVARIANT_BROKEN", "STATE_CORRUPTION"}:
        return "contract_violation"
    if code in {"PROVIDER_EXHAUSTED", "PROVIDER_UNAVAILABLE", "PROVIDER_NETWORK"}:
        return "unknown"
    return "unknown"


def _decision_diagnosis(reflection: Any) -> str:
    """Map Reflection's root-cause vocabulary to Decision's policy keys."""
    return {
        "tool": "tool_failure",
        "grounding": "grounding_miss",
        "planning": "planning_failure",
        "decision": "decision_failure",
        "prompt": "prompt_failure",
        "runtime": "runtime_failure",
        "external": "external_failure",
    }.get(reflection.diagnosis.root_cause, "unknown")


class FailurePolicy:
    """Resolve structural failure facts without owning execution side effects."""

    def resolve(
        self,
        failure: FailureFact,
        state: Mapping[str, Any] | None = None,
    ) -> RecoveryDirective:
        if failure.kind is not FailureKind.STRUCTURAL:
            return RecoveryDirective(
                action=OBSERVE,
                reason="ordinary action failure remains an observation",
                failure=failure,
            )

        # These imports are intentionally local.  ActionResult and ordinary
        # executors import agent.failure for taxonomy only; they must not load
        # the Reflection/Decision graph on every action.
        from agent.decision.decision import (
            DecisionInput,
            ExecutionState,
            decide,
        )
        from agent.reflection.reflector import reflect

        symptom = _symptom_for(failure)
        event = FailureEvent(
            benchmark="runtime",
            scenario=failure.code,
            layer=failure.component or "runtime",
            dimension=failure.kind.value.lower(),
            failure=failure.message or failure.code,
            evidence=[Evidence(
                source=failure.component or "runtime",
                location=failure.code,
                expected="structural failure handled by FailurePolicy",
                actual=failure.message or failure.code,
            )],
            symptom=symptom if symptom in SYMPTOM_MAP else "unknown",
        )
        reflection = reflect(event)
        diagnosis = _decision_diagnosis(reflection)
        runtime_state = state or {}
        decision, trace = decide(
            DecisionInput(
                diagnosis=diagnosis,
                diagnosis_confidence=reflection.diagnosis.confidence,
                state=ExecutionState(
                    retry_count=int(runtime_state.get("retries", 0) or 0),
                    same_tool=bool(runtime_state.get("last_tool", "")),
                    user_blocked=False,
                    evidence_completeness=(
                        1.0 if runtime_state.get("execution_evidence") else 0.5
                    ),
                ),
                event_id=event.id,
            )
        )
        return RecoveryDirective(
            action=decision.action,
            reason=decision.reason,
            failure=failure,
            diagnosis=reflection.diagnosis.root_cause,
            correction=reflection.correction.action,
            confidence=decision.confidence,
            event_id=event.id,
            decision_id=trace.decision_id,
        )


__all__ = [
    "FailurePolicy",
    "OBSERVE",
    "RECOVERY_ACTIONS",
    "RecoveryDirective",
]
