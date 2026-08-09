"""Three-layer P2-L report: raw evidence, deterministic oracle, outcome."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from benchmarks.p2.cases import P2Case
from benchmarks.p2.metadata import benchmark_metadata, dataset_hash
from benchmarks.p2.oracle import evaluate

from .evidence import RunTraceEvidence
from .invariants import RuntimeInvariantResult, evaluate_runtime_invariants


@dataclass(frozen=True)
class LongHorizonResult:
    case: P2Case
    trace: RunTraceEvidence
    invariants: RuntimeInvariantResult
    capability_outcome: str
    capability_detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case.id,
            "capability": {
                "outcome": self.capability_outcome,
                "detail": self.capability_detail,
            },
            "runtime": self.invariants.to_dict(),
            "performance": self.trace.performance.to_dict(),
            "evidence": self.trace.to_dict(),
            "oracle": evaluate(self.case).to_dict(),
        }


def make_result(
    case: P2Case,
    trace: RunTraceEvidence,
    *,
    capability_outcome: str,
    capability_detail: str,
) -> LongHorizonResult:
    if capability_outcome not in {"PASS", "FAIL", "PARTIAL"}:
        raise ValueError(f"{case.id}: invalid capability outcome {capability_outcome!r}")
    return LongHorizonResult(
        case=case,
        trace=trace,
        invariants=evaluate_runtime_invariants(trace),
        capability_outcome=capability_outcome,
        capability_detail=capability_detail,
    )


def build_report(
    results: tuple[LongHorizonResult, ...],
    *,
    source: str,
    commit: str,
    attempts: int = 1,
) -> dict[str, Any]:
    cases = tuple(result.case for result in results)
    runtime_pass = sum(result.invariants.runtime_correctness == "PASS" for result in results)
    capability_pass = sum(result.capability_outcome == "PASS" for result in results)
    return {
        "suite": "P2-L Long-horizon Harness",
        "source": source,
        "commit": commit,
        "attempts_per_case": attempts,
        "automatic_rerun": False,
        "scope": (
            "fixture evidence/oracle/report pipeline only"
            if source == "fixture"
            else "one real AgentService attempt; no automatic rerun"
        ),
        "dataset": benchmark_metadata(cases),
        "dataset_hash": dataset_hash(cases),
        "summary": {
            "total": len(results),
            "capability_pass": capability_pass,
            "runtime_correctness_pass": runtime_pass,
            "runtime_correctness_rate": (runtime_pass / len(results) if results else 0.0),
            "provider_error_count": sum(len(result.trace.provider_errors) for result in results),
        },
        "results": [result.to_dict() for result in results],
    }


__all__ = ["LongHorizonResult", "build_report", "make_result"]
