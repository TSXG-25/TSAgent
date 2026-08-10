"""Validate v2.3D-1 Dataset, policy coverage, and deterministic Oracle."""

from __future__ import annotations

import json
import sys
from collections import Counter

from agent.interruption import InterruptionReason, interruption_policy_contract

from .cases import D1Group, HARD_GATES, Probe, build_cases, InterruptionContractCase
from .metadata import benchmark_metadata, dataset_hash
from .oracle import evaluate


EXPECTED_GROUP_COUNTS = {
    D1Group.REQUEST_LIFECYCLE: 4,
    D1Group.SAFE_BOUNDARY: 4,
    D1Group.RESTART_RESUME: 4,
    D1Group.TIMEOUT_POLICY: 2,
    D1Group.CLIENT_LIFECYCLE: 1,
    D1Group.COMMITTED_EFFECT: 1,
}


def validate_case(case: InterruptionContractCase) -> list[str]:
    problems: list[str] = []
    try:
        restored = InterruptionContractCase.from_dict(case.to_dict())
    except (TypeError, ValueError) as error:
        problems.append(f"{case.id}: case round-trip failed: {error}")
    else:
        if restored != case:
            problems.append(f"{case.id}: case round-trip changed data")
    first = evaluate(case)
    second = evaluate(case)
    if first.outcome is not case.expected_outcome:
        problems.append(
            f"{case.id}: expected {case.expected_outcome.value}, got {first.outcome.value}"
        )
    if json.dumps(first.to_dict(), sort_keys=True) != json.dumps(
        second.to_dict(), sort_keys=True
    ):
        problems.append(f"{case.id}: Oracle is not deterministic")
    return problems


def validate(
    cases: tuple[InterruptionContractCase, ...] | None = None,
) -> list[str]:
    values = build_cases() if cases is None else cases
    problems: list[str] = []
    if len(values) != 16:
        problems.append(f"expected 16 cases, got {len(values)}")
    ids = [case.id for case in values]
    expected_ids = [f"C{index:02d}" for index in range(1, 17)]
    if ids != expected_ids:
        problems.append("case ids must be the ordered C01-C16 manifest")
    duplicates = sorted(case_id for case_id, count in Counter(ids).items() if count > 1)
    if duplicates:
        problems.append(f"duplicate case ids: {', '.join(duplicates)}")
    groups = Counter(case.group for case in values)
    for group, expected in EXPECTED_GROUP_COUNTS.items():
        if groups[group] != expected:
            problems.append(f"{group.value}: expected {expected}, got {groups[group]}")
    if {case.probe for case in values} != set(Probe):
        problems.append("every frozen Probe must appear exactly once")
    covered_gates = frozenset(gate for case in values for gate in case.hard_gates)
    if covered_gates != HARD_GATES:
        problems.append("Dataset must cover every v2.3D hard gate")
    policies = interruption_policy_contract()
    if set(policies) != {reason.value for reason in InterruptionReason}:
        problems.append("interruption policy must cover every reason")
    for case in values:
        problems.extend(validate_case(case))
    if dataset_hash(values) != dataset_hash(tuple(reversed(values))):
        problems.append("dataset hash must be order-independent")
    return problems


def main() -> int:
    cases = build_cases()
    problems = validate(cases)
    if problems:
        print(f"v2.3D Cancellation/Timeout Validation: FAIL ({len(problems)} issues)")
        for problem in problems:
            print(f"- {problem}")
        return 1
    metadata = benchmark_metadata(cases)
    print(
        "v2.3D Cancellation/Timeout Validation: PASS "
        f"({metadata['case_count']} cases; dataset_hash={metadata['dataset_hash']})"
    )
    print("Scope: D1 Contract/Dataset/Oracle only; cancel_run implementation is deferred")
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["main", "validate", "validate_case"]
