"""Validate the v2.3C Contract/Dataset/Oracle boundary."""

from __future__ import annotations

import json
import sys
from collections import Counter

from .cases import ServiceContractCase, build_cases
from .metadata import benchmark_metadata
from .oracle import evaluate


REQUIRED_GROUPS = frozenset(
    {"identity", "dto", "idempotency", "event_ordering", "event_replay", "terminal_state"}
)


def validate(cases: tuple[ServiceContractCase, ...]) -> list[str]:
    problems: list[str] = []
    ids = [case.id for case in cases]
    duplicates = sorted(case_id for case_id, count in Counter(ids).items() if count > 1)
    if duplicates:
        problems.append(f"duplicate case ids: {', '.join(duplicates)}")
    if len(cases) != 16:
        problems.append(f"expected 16 cases, got {len(cases)}")
    missing_groups = sorted(REQUIRED_GROUPS - {case.group for case in cases})
    if missing_groups:
        problems.append(f"missing required groups: {', '.join(missing_groups)}")
    for case in cases:
        if not case.must_not:
            problems.append(f"{case.id}: must_not is empty")
        decision = evaluate(case)
        if decision.outcome is not case.expected_outcome:
            problems.append(
                f"{case.id}: expected {case.expected_outcome.value}, "
                f"got {decision.outcome.value}"
            )
        try:
            restored = ServiceContractCase.from_dict(case.to_dict())
        except (TypeError, ValueError) as error:
            problems.append(f"{case.id}: case round-trip failed: {error}")
        else:
            if restored != case:
                problems.append(f"{case.id}: case round-trip changed data")
    first = [evaluate(case).to_dict() for case in cases]
    second = [evaluate(case).to_dict() for case in cases]
    if first != second:
        problems.append("oracle is not deterministic")
    if json.dumps(first, sort_keys=True, ensure_ascii=False) != json.dumps(
        second, sort_keys=True, ensure_ascii=False
    ):
        problems.append("oracle JSON output is not deterministic")
    return problems


def main() -> int:
    cases = build_cases()
    problems = validate(cases)
    if problems:
        print("AgentService Contract Benchmark Validation: FAIL")
        for problem in problems:
            print(f"- {problem}")
        return 1
    metadata = benchmark_metadata(cases)
    print(
        "AgentService Contract Benchmark Validation: PASS "
        f"({metadata['case_count']} cases, dataset_hash={metadata['dataset_hash']})"
    )
    print("Scope: C-1 contract/dataset/oracle only; concrete Service is deferred to C-2")
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["main", "validate"]
