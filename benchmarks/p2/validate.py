"""Validate the P2 Dataset, provenance, and two-layer scoring oracle."""
from __future__ import annotations

import json
import sys
from collections import Counter

from .cases import BENCHMARK_VERSION, HARD_GATES, P2Case, P2Group, build_cases
from .metadata import benchmark_metadata
from .oracle import evaluate


EXPECTED_GROUP_COUNTS = {
    P2Group.LONG_HORIZON: 5,
    P2Group.RESTART: 4,
    P2Group.SOAK: 4,
    P2Group.PORTABILITY: 3,
}


def validate_case(case: P2Case) -> list[str]:
    problems: list[str] = []
    decision = evaluate(case)
    if decision.required_runtime_outcome.value != "PASS":
        problems.append(f"{case.id}: runtime correctness must be a hard PASS expectation")
    if not set(decision.hard_gates).issubset(HARD_GATES):
        problems.append(f"{case.id}: oracle contains an unknown hard gate")
    try:
        restored = P2Case.from_dict(case.to_dict())
    except (TypeError, ValueError) as error:
        problems.append(f"{case.id}: case round-trip failed: {error}")
    else:
        if restored != case:
            problems.append(f"{case.id}: case round-trip changed data")
    repeated = evaluate(case)
    if json.dumps(decision.to_dict(), sort_keys=True, ensure_ascii=False) != json.dumps(
        repeated.to_dict(), sort_keys=True, ensure_ascii=False
    ):
        problems.append(f"{case.id}: oracle is not deterministic")
    if case.group is P2Group.PORTABILITY:
        if len(case.provider_variants) < 2 or not case.provider_parity_key:
            problems.append(f"{case.id}: provider parity requires two variants and a parity key")
        if case.reprompt_allowed:
            problems.append(f"{case.id}: portability comparison must not reprompt")
    return problems


def validate(cases: tuple[P2Case, ...] | None = None) -> list[str]:
    cases = cases if cases is not None else build_cases()
    problems: list[str] = []
    if len(cases) != 16:
        problems.append(f"expected 16 cases, got {len(cases)}")
    ids = [case.id for case in cases]
    duplicates = sorted(case_id for case_id, count in Counter(ids).items() if count > 1)
    if duplicates:
        problems.append(f"duplicate case ids: {', '.join(duplicates)}")
    counts = Counter(case.group for case in cases)
    for group, expected in EXPECTED_GROUP_COUNTS.items():
        if counts[group] != expected:
            problems.append(f"{group.value}: expected {expected}, got {counts[group]}")
    expected_prefixes = {
        P2Group.LONG_HORIZON: "L",
        P2Group.RESTART: "R",
        P2Group.SOAK: "S",
        P2Group.PORTABILITY: "P",
    }
    for case in cases:
        if not case.id.startswith(expected_prefixes[case.group]):
            problems.append(f"{case.id}: id does not match group {case.group.value}")
        problems.extend(validate_case(case))
    parity_keys = [case.provider_parity_key for case in cases if case.provider_parity_key]
    if len(parity_keys) != len(set(parity_keys)):
        problems.append("provider parity keys must be unique")
    if any(case.provider_parity_key and case.mode.value != "real_provider" for case in cases):
        problems.append("provider parity cases must use real_provider validation mode")
    return problems


def main() -> int:
    cases = build_cases()
    problems = validate(cases)
    if problems:
        print(f"P2 Runtime Endurance/Portability Validation: FAIL ({len(problems)} issues)")
        for problem in problems:
            print(f"- {problem}")
        return 1
    metadata = benchmark_metadata(cases)
    print(
        "P2 Runtime Endurance/Portability Validation: PASS "
        f"({metadata['case_count']} cases; version={BENCHMARK_VERSION}; "
        f"dataset_hash={metadata['dataset_hash']})"
    )
    print("Scope: Contract/Dataset/Oracle only; runtime execution is deferred to P2 implementation phases")
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["main", "validate", "validate_case"]
