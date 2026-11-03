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


ACCEPTANCE_CRITERION_KINDS = frozenset({
    "file_contains",
    "files_distinct",
    "reply_mentions_artifact_path",
    "unverifiable",
})


@dataclass(frozen=True)
class AcceptanceCriterion:
    """One typed, machine-checkable acceptance fact declared by the Intent."""

    kind: str
    text: str = ""
    description: str = ""

    def __post_init__(self) -> None:
        if self.kind not in ACCEPTANCE_CRITERION_KINDS:
            raise ValueError(f"unsupported acceptance criterion kind: {self.kind}")
        if self.kind == "file_contains" and not self.text.strip():
            raise ValueError("file_contains requires non-empty text")
        if self.kind == "unverifiable" and not self.description.strip():
            raise ValueError("unverifiable requires a description")

    @property
    def verifiable(self) -> bool:
        return self.kind != "unverifiable"

    def key(self) -> str:
        """Stable evidence identity: (deliverable_id, key) pairs."""
        payload = self.text or self.description
        return f"{self.kind}:{payload}" if payload else self.kind

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {"kind": self.kind}
        if self.text:
            value["text"] = self.text
        if self.description:
            value["description"] = self.description
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AcceptanceCriterion":
        if not isinstance(value.get("kind"), str):
            raise ValueError("acceptance criterion kind must be text")
        return cls(
            kind=value["kind"],
            text=str(value.get("text", "") or ""),
            description=str(value.get("description", "") or ""),
        )


@dataclass(frozen=True)
class DeliveryRequirement:
    """One explicitly requested, countable persistent deliverable."""

    id: str
    kind: str
    quantity: int
    target: str = ""
    acceptance_criteria: tuple[AcceptanceCriterion, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("delivery requirement id must be non-empty")
        if self.kind != "file":
            raise ValueError(f"unsupported deliverable kind: {self.kind}")
        if type(self.quantity) is not int or self.quantity < 1:
            raise ValueError("delivery requirement quantity must be a positive integer")
        if not isinstance(self.target, str):
            raise ValueError("delivery requirement target must be text")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "quantity": self.quantity,
            "target": self.target,
            "acceptance_criteria": [
                item.to_dict() for item in self.acceptance_criteria
            ],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DeliveryRequirement":
        for key in ("id", "kind", "target"):
            if not isinstance(value.get(key), str):
                raise ValueError(f"delivery requirement {key} must be text")
        criteria = value.get("acceptance_criteria") or []
        if not isinstance(criteria, list) or any(
            not isinstance(item, Mapping) for item in criteria
        ):
            raise ValueError("acceptance_criteria must be a list of objects")
        return cls(
            id=value["id"],
            kind=value["kind"],
            quantity=value.get("quantity"),
            target=value["target"],
            acceptance_criteria=tuple(
                AcceptanceCriterion.from_dict(item) for item in criteria
            ),
        )


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
    """Versioned goal contract and its bounded Runtime lifecycle projection."""

    objective: str
    status: GoalStatus = GoalStatus.ACTIVE
    round: int = 0
    max_rounds: int = field(
        default_factory=lambda: max(1, int(os.getenv("TSAGENT_MAX_GOAL_ROUNDS", "10")))
    )
    completion_evidence: tuple[GoalEvidence, ...] = ()
    blocker: str = ""
    original_request: str = ""
    goal_id: str = ""
    goal_version: int = 1
    requested_outcomes: tuple[str, ...] = ()
    delivery_requirements: tuple[DeliveryRequirement, ...] = ()
    prohibited_effects: tuple[str, ...] = ()
    acceptance_criteria: tuple[AcceptanceCriterion, ...] = ()

    def __post_init__(self) -> None:
        if self.goal_version < 1:
            raise ValueError("goal_version must be positive")
        ids = [item.id for item in self.delivery_requirements]
        if len(ids) != len(set(ids)):
            raise ValueError("delivery requirement ids must be unique")

    def with_contract(
        self,
        *,
        original_request: str,
        objective: str,
        requested_outcomes: tuple[str, ...],
        delivery_requirements: tuple[DeliveryRequirement, ...],
        prohibited_effects: tuple[str, ...],
        acceptance_criteria: tuple[AcceptanceCriterion, ...],
        goal_id: str,
        goal_version: int | None = None,
    ) -> "GoalState":
        """Install the semantic contract; advance version only on a real revision."""
        changed = bool(self.original_request) and (
            self.original_request != original_request
            or self.objective != objective
            or self.requested_outcomes != requested_outcomes
            or self.delivery_requirements != delivery_requirements
            or self.prohibited_effects != prohibited_effects
            or self.acceptance_criteria != acceptance_criteria
        )
        return replace(
            self,
            objective=objective,
            original_request=original_request,
            goal_id=goal_id,
            goal_version=(
                goal_version
                if goal_version is not None
                else self.goal_version + int(changed)
            ),
            status=GoalStatus.ACTIVE if changed else self.status,
            completion_evidence=() if changed else self.completion_evidence,
            blocker="" if changed else self.blocker,
            requested_outcomes=requested_outcomes,
            delivery_requirements=delivery_requirements,
            prohibited_effects=prohibited_effects,
            acceptance_criteria=acceptance_criteria,
        )

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
            "original_request": self.original_request,
            "goal_id": self.goal_id,
            "goal_version": self.goal_version,
            "requested_outcomes": list(self.requested_outcomes),
            "delivery_requirements": [
                item.to_dict() for item in self.delivery_requirements
            ],
            "prohibited_effects": list(self.prohibited_effects),
            "acceptance_criteria": [
                item.to_dict() for item in self.acceptance_criteria
            ],
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
        status = GoalStatus(status_value)
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
            original_request=str(data.get("original_request", "")),
            goal_id=str(data.get("goal_id", "")),
            goal_version=max(1, int(data.get("goal_version", 1))),
            requested_outcomes=tuple(
                str(item) for item in data.get("requested_outcomes", ())
            ),
            delivery_requirements=tuple(
                DeliveryRequirement.from_dict(item)
                for item in data.get("delivery_requirements", ())
            ),
            prohibited_effects=tuple(
                str(item) for item in data.get("prohibited_effects", ())
            ),
            acceptance_criteria=tuple(
                AcceptanceCriterion.from_dict(item)
                for item in data.get("acceptance_criteria", ())
            ),
        )


class GoalVerifier:
    """Verify whole-goal completion from Runtime facts."""

    @staticmethod
    def delivery_paths(state: Mapping[str, Any]) -> dict[str, tuple[Mapping[str, Any], ...]]:
        """Assign each verified file to at most one delivery requirement."""
        goal = GoalState.from_dict(state.get("goal_state"))
        verified_paths = {
            str(path) for path in state.get("verified_artifact_paths", [])
        }
        available = {
            str(item["path"]): item
            for item in state.get("file_artifact_evidence", [])
            if isinstance(item, Mapping)
            and str(item.get("path", "")) in verified_paths
            and str(item.get("digest", ""))
        }
        assignments: dict[str, tuple[Mapping[str, Any], ...]] = {}
        for requirement in goal.delivery_requirements:
            candidates = [
                path for path in available
                if not requirement.target
                or (
                    path.startswith(requirement.target.rstrip("/") + "/")
                    if requirement.target.endswith("/")
                    else path == requirement.target
                )
            ]
            selected = candidates[: requirement.quantity]
            assignments[requirement.id] = tuple(available.pop(path) for path in selected)
        return assignments

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
        goal = GoalState.from_dict(state.get("goal_state"))

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

        if state.get("artifact_required"):
            if not goal.delivery_requirements:
                missing.append("delivery requirements")
            assignments = GoalVerifier.delivery_paths(state)
            for requirement in goal.delivery_requirements:
                if len(assignments[requirement.id]) < requirement.quantity:
                    missing.append(
                        f"deliverable:{requirement.id}:{requirement.quantity}"
                    )
                    continue
                evidence.append(
                    GoalEvidence(
                        "deliverable_verifier",
                        f"{requirement.id}: {requirement.quantity} distinct verified file(s)",
                    )
                )

        acceptance_evidence = {
            (str(item.get("deliverable_id", "")), str(item.get("criterion", "")))
            for item in state.get("acceptance_evidence", [])
            if isinstance(item, Mapping) and item.get("verified") is True
        }
        for criterion in goal.acceptance_criteria:
            if not criterion.verifiable:
                continue
            if ("", criterion.key()) not in acceptance_evidence:
                missing.append(f"acceptance:{criterion.key()}")
            else:
                evidence.append(
                    GoalEvidence("acceptance_verifier", criterion.key())
                )
        for requirement in goal.delivery_requirements:
            for criterion in requirement.acceptance_criteria:
                if not criterion.verifiable:
                    continue
                if (requirement.id, criterion.key()) not in acceptance_evidence:
                    missing.append(
                        f"acceptance:{requirement.id}:{criterion.key()}"
                    )
                else:
                    evidence.append(
                        GoalEvidence(
                            "acceptance_verifier",
                            f"{requirement.id}: {criterion.key()}",
                        )
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
    "ACCEPTANCE_CRITERION_KINDS",
    "AcceptanceCriterion",
    "CompletionDecision",
    "DeliveryRequirement",
    "GoalEvidence",
    "GoalState",
    "GoalStatus",
    "GoalVerifier",
]
