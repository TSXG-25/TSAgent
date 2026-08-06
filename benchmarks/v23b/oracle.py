"""Pure transaction/crash oracle for the v2.3B Dataset.

This is an evaluation oracle, not a SQLite implementation.  The production
Store must later produce evidence matching these outcomes.
"""
from __future__ import annotations

from dataclasses import dataclass

from .cases import CrashTrigger, OracleOutcome, StoreCrashCase, VisibleState


@dataclass(frozen=True)
class OracleDecision:
    outcome: OracleOutcome
    visible_state: VisibleState
    preserves: tuple[str, ...]
    forbids: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "outcome": self.outcome.value,
            "visible_state": self.visible_state.value,
            "preserves": list(self.preserves),
            "forbids": list(self.forbids),
        }


def evaluate(case: StoreCrashCase) -> OracleDecision:
    """Return the deterministic Store contract outcome for one case."""
    trigger = case.trigger
    rollback_triggers = {
        CrashTrigger.AFTER_CHECKPOINT_INSERT,
        CrashTrigger.AFTER_ARTIFACT_METADATA,
        CrashTrigger.AFTER_INDEX_UPDATE,
        CrashTrigger.BEFORE_COMMIT,
    }
    if trigger is CrashTrigger.NONE:
        outcome, state = OracleOutcome.COMMITTED, VisibleState.NEW
    elif trigger is CrashTrigger.BEFORE_BEGIN:
        outcome, state = OracleOutcome.NO_CHANGE, VisibleState.PREVIOUS
    elif trigger is CrashTrigger.PREPARATION_BEFORE_COMMIT:
        outcome, state = OracleOutcome.ROLLED_BACK, VisibleState.PREVIOUS
    elif trigger is CrashTrigger.AFTER_PREPARATION_COMMIT:
        outcome, state = OracleOutcome.PREPARED, VisibleState.NEW
    elif trigger in rollback_triggers:
        outcome, state = OracleOutcome.ROLLED_BACK, VisibleState.PREVIOUS
    elif trigger is CrashTrigger.AFTER_COMMIT_BEFORE_RESPONSE:
        outcome, state = OracleOutcome.IDEMPOTENT_RETRY, VisibleState.NEW
    elif trigger is CrashTrigger.IDEMPOTENCY_SAME_KEY_SAME_DIGEST:
        outcome, state = OracleOutcome.IDEMPOTENT_RETRY, VisibleState.NEW
    elif trigger is CrashTrigger.IDEMPOTENCY_SAME_KEY_DIFFERENT_DIGEST:
        outcome, state = OracleOutcome.IDEMPOTENCY_CONFLICT, VisibleState.PREVIOUS
    elif trigger is CrashTrigger.DIFFERENT_KEY:
        outcome, state = OracleOutcome.COMMITTED, VisibleState.NEW
    elif trigger is CrashTrigger.FENCE_TAKEOVER:
        outcome, state = OracleOutcome.FENCE_ACQUIRED, VisibleState.NEW
    elif trigger in {CrashTrigger.REVISION_CONFLICT, CrashTrigger.STALE_WRITER}:
        outcome, state = OracleOutcome.REJECTED, VisibleState.PREVIOUS
    elif trigger is CrashTrigger.SIDE_EFFECT_BEFORE_FINALIZATION:
        outcome, state = OracleOutcome.RECONCILE_REQUIRED, VisibleState.EXTERNAL_RECONCILIATION
    elif trigger is CrashTrigger.UNKNOWN_EXTERNAL_RESULT:
        outcome, state = OracleOutcome.REQUIRE_CLARIFICATION, VisibleState.BLOCKED
    elif trigger is CrashTrigger.PROCESS_RESTART_AFTER_COMMIT:
        outcome, state = OracleOutcome.RECOVERED, VisibleState.NEW
    elif trigger is CrashTrigger.READ_DURING_COMMIT:
        outcome, state = OracleOutcome.CONSISTENT_SNAPSHOT, VisibleState.PREVIOUS_OR_NEW
    else:  # pragma: no cover - enum exhaustiveness guard
        raise ValueError(f"unsupported crash trigger: {trigger}")

    return OracleDecision(
        outcome=outcome,
        visible_state=state,
        preserves=case.must_preserve,
        forbids=case.must_not,
    )


__all__ = ["OracleDecision", "evaluate"]
