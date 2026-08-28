"""Goal-level completion facts for the result-driven runtime.

Tasks and plans are execution hints. A goal is complete only when this module
finds evidence for every required outcome and the user-visible result exists.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
import os
from typing import Any, Mapping

from agent.effect_truth import execution_truth
from agent.runtime_gates import has_fresh_evidence, output_required


class GoalStatus(str, Enum):
    """Durable-style goal projection statuses."""

    ACTIVE = "ACTIVE"
    COMPLETE = "COMPLETE"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    WAITING_USER = "WAITING_USER"


@dataclass(frozen=True)
class GoalEvidence:
    """One machine-verifiable fact used by the goal gate."""

    source: str
    detail: str
    verified: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "detail": self.detail,
            "verified": self.verified,
        }


@dataclass(frozen=True)
class CompletionDecision:
    """GoalVerifier output; prose cannot replace this decision."""

    status: GoalStatus
    evidence: tuple[GoalEvidence, ...] = ()
    missing: tuple[str, ...] = ()
    blocker: str = ""

    @property
    def can_complete(self) -> bool:
        return self.status is GoalStatus.COMPLETE and not self.missing

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "evidence": [item.to_dict() for item in self.evidence],
            "missing": list(self.missing),
            "blocker": self.blocker,
            "can_complete": self.can_complete,
        }


@dataclass(frozen=True)
class GoalState:
    """Ephemeral projection of one user objective and its bounded rounds."""

    objective: str
    status: GoalStatus = GoalStatus.ACTIVE
    round: int = 0
    max_rounds: int = field(
        default_factory=lambda: max(1, int(os.getenv("TSAGENT_MAX_GOAL_ROUNDS", "10")))
    )
    completion_evidence: tuple[GoalEvidence, ...] = ()
    blocker: str = ""

    def next_round(self) -> "GoalState":
        """Advance one bounded reasoning round."""
        if self.round >= self.max_rounds:
            return replace(
                self,
                status=GoalStatus.FAILED,
                blocker="GOAL_ROUND_LIMIT",
            )
        return replace(self, round=self.round + 1)

    def with_decision(self, decision: CompletionDecision) -> "GoalState":
        """Project a verifier decision onto the goal state."""
        return replace(
            self,
            status=decision.status,
            completion_evidence=decision.evidence,
            blocker=decision.blocker,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "objective": self.objective,
            "status": self.status.value,
            "round": self.round,
            "max_rounds": self.max_rounds,
            "completion_evidence": [
                evidence.to_dict() for evidence in self.completion_evidence
            ],
            "blocker": self.blocker,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | None) -> "GoalState":
        """Restore the state projection from AgentState."""
        data = dict(value or {})
        status_value = str(data.get("status", GoalStatus.ACTIVE.value))
        try:
            status = GoalStatus(status_value)
        except ValueError:
            status = GoalStatus.ACTIVE
        evidence = tuple(
            GoalEvidence(
                source=str(item.get("source", "")),
                detail=str(item.get("detail", "")),
                verified=bool(item.get("verified", True)),
            )
            for item in data.get("completion_evidence", ())
            if isinstance(item, Mapping)
        )
        return cls(
            objective=str(data.get("objective", "")),
            status=status,
            round=max(0, int(data.get("round", 0))),
            max_rounds=max(1, int(data.get("max_rounds", 10))),
            completion_evidence=evidence,
            blocker=str(data.get("blocker", "")),
        )


class GoalVerifier:
    """Verify whole-goal completion from Runtime facts."""

    @staticmethod
    def verify(state: Mapping[str, Any], answer: str = "") -> CompletionDecision:
        """Return a completion decision without consulting the LLM."""
        evidence: list[GoalEvidence] = []
        missing: list[str] = []
        tasks = [item for item in (state.get("plan") or []) if isinstance(item, Mapping)]
        failed = [item for item in tasks if str(item.get("status", "")) == "failed"]
        pending = [
            item for item in tasks
            if str(item.get("status", "pending")) not in {"succeeded", "skipped"}
        ]
        truth = execution_truth(state)
        runtime_status = str(state.get("runtime_terminal_status", "") or "")
        failure_code = str(state.get("runtime_failure_code", "") or "")

        if truth.verified_effects:
            evidence.append(GoalEvidence("effect_verifier", "verified effects present"))
        if truth.execution_evidence:
            evidence.append(GoalEvidence("execution_evidence", "execution evidence present"))
        if tasks and not failed and not pending:
            evidence.append(GoalEvidence("task_set", "all planned tasks reached a terminal success state"))

        if failed:
            missing.append("failed task evidence")
        elif pending:
            missing.append("pending task completion")
        if truth.unresolved_required_effects:
            missing.extend(
                f"effect:{item.get('effect_id', 'unknown')}"
                for item in truth.unresolved_required_effects
            )
        if truth.unresolved_requested_outcomes:
            missing.extend(
                f"outcome:{item}" for item in truth.unresolved_requested_outcomes
            )

        answer_required = output_required(state)
        if answer_required and not str(answer or "").strip():
            missing.append("user-visible output")
        if (
            bool(state.get("freshness_required", False))
            or bool(state.get("source_grounding_required", False))
        ) and not has_fresh_evidence(state):
            missing.append("fresh external evidence")

        if runtime_status in {"CANCELLED", "TIMED_OUT"}:
            return CompletionDecision(
                GoalStatus.FAILED,
                tuple(evidence),
                tuple(dict.fromkeys(missing or [runtime_status])),
                runtime_status,
            )
        if truth.unsupported_effects:
            return CompletionDecision(
                GoalStatus.BLOCKED,
                tuple(evidence),
                tuple(dict.fromkeys(missing or ["unsupported capability"])),
                "UNSUPPORTED_CAPABILITY",
            )
        if missing:
            status = (
                GoalStatus.BLOCKED
                if failure_code in {
                    "RESEARCH_TOOL_UNAVAILABLE",
                    "UNSUPPORTED_CAPABILITY",
                }
                or "fresh external evidence" in missing
                else GoalStatus.FAILED
            )
            return CompletionDecision(
                status,
                tuple(evidence),
                tuple(dict.fromkeys(missing)),
                failure_code or "GOAL_INCOMPLETE",
            )
        if runtime_status in {"FAILED_TERMINAL", "BLOCKED"} or failure_code:
            return CompletionDecision(
                GoalStatus.BLOCKED if runtime_status == "BLOCKED" else GoalStatus.FAILED,
                tuple(evidence),
                (failure_code or "runtime failure",),
                failure_code or runtime_status,
            )
        evidence.append(GoalEvidence("completion_gate", "all required goal evidence present"))
        return CompletionDecision(GoalStatus.COMPLETE, tuple(evidence))


__all__ = [
    "CompletionDecision",
    "GoalEvidence",
    "GoalState",
    "GoalStatus",
    "GoalVerifier",
]
