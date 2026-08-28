"""Small, dependency-free failure facts used by production Runtime.

The evaluation Fail Board consumes these contracts, but production code must
not import the evaluation package.  This module intentionally contains facts
and taxonomy only; lifecycle aggregation remains an evaluation concern.
"""

from dataclasses import dataclass, field
from typing import Dict, List


SYMPTOM_MAP: Dict[str, dict] = {
    "timeout": {"root_cause": "tool", "correction": "switch_tool"},
    "wrong_answer": {"root_cause": "decision", "correction": "re_decide"},
    "missing_constraint": {"root_cause": "planning", "correction": "replanning"},
    "hallucination": {"root_cause": "grounding", "correction": "re_ground"},
    "context_drift": {"root_cause": "planning", "correction": "replanning"},
    "contract_violation": {
        "root_cause": "integration",
        "correction": "repair_contract",
    },
    "unknown": {"root_cause": "unknown", "correction": "ask_user"},
}
VALID_SYMPTOMS = frozenset(SYMPTOM_MAP)


@dataclass(frozen=True)
class Evidence:
    """Structured evidence consumed by Reflection."""

    source: str
    location: str
    expected: str
    actual: str


@dataclass(frozen=True)
class FailureEvent:
    """Immutable failure fact shared by Runtime and evaluation."""

    benchmark: str
    scenario: str
    layer: str
    dimension: str
    failure: str
    evidence: List[Evidence] = field(default_factory=list)
    symptom: str = "unknown"
    detected_at: str = ""

    def __post_init__(self) -> None:
        if self.symptom not in VALID_SYMPTOMS:
            raise ValueError(
                f"非法 symptom: {self.symptom!r}（合法: {sorted(VALID_SYMPTOMS)}）"
            )

    @property
    def id(self) -> str:
        return f"{self.benchmark}:{self.scenario}:{self.dimension}"


__all__ = [
    "Evidence",
    "FailureEvent",
    "SYMPTOM_MAP",
    "VALID_SYMPTOMS",
]
