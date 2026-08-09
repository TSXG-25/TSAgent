"""Pure P2 contract oracle; no Runtime, Provider, or filesystem access."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .cases import CapabilityTarget, P2Case, RuntimeExpectation


@dataclass(frozen=True)
class P2OracleDecision:
    case_id: str
    capability_target: CapabilityTarget
    required_runtime_outcome: RuntimeExpectation
    hard_gates: tuple[str, ...]
    performance_profile: str
    performance_metrics: tuple[str, ...]
    provider_parity_key: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "capability_target": self.capability_target.value,
            "required_runtime_outcome": self.required_runtime_outcome.value,
            "hard_gates": list(self.hard_gates),
            "performance_profile": self.performance_profile,
            "performance_metrics": list(self.performance_metrics),
            "provider_parity_key": self.provider_parity_key,
        }


def evaluate(case: P2Case) -> P2OracleDecision:
    """Translate a case manifest into its deterministic acceptance contract."""
    return P2OracleDecision(
        case_id=case.id,
        capability_target=case.capability_target,
        required_runtime_outcome=case.runtime_expectation,
        hard_gates=case.hard_gates,
        performance_profile=case.performance_profile,
        performance_metrics=case.performance_metrics,
        provider_parity_key=case.provider_parity_key or None,
    )


__all__ = ["P2OracleDecision", "evaluate"]
