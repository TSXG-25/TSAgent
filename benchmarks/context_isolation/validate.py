#!/usr/bin/env python3
"""Validate the v2.3A failure-oriented Dataset.

This command validates the benchmark contract only.  A PASS here does not
claim that the production runtime already satisfies the isolation invariants.
"""

from __future__ import annotations

import sys
from collections import Counter

from .cases import IsolationCase, build_cases
from .metadata import benchmark_metadata


REQUIRED_GROUPS = frozenset(
    {
        "artifact_isolation",
        "event_isolation",
        "event_lifecycle",
        "session_isolation",
        "resource_lifecycle",
        "ownership",
    }
)


def validate(cases: tuple[IsolationCase, ...]) -> list[str]:
    """Return deterministic Dataset contract violations."""

    problems: list[str] = []
    ids = [case.id for case in cases]
    duplicate_ids = sorted(
        case_id for case_id, count in Counter(ids).items() if count > 1
    )
    if duplicate_ids:
        problems.append(f"duplicate case ids: {', '.join(duplicate_ids)}")

    if len(cases) != 12:
        problems.append(f"expected 12 cases, got {len(cases)}")

    groups = {case.group for case in cases}
    missing_groups = sorted(REQUIRED_GROUPS - groups)
    if missing_groups:
        problems.append(f"missing required groups: {', '.join(missing_groups)}")

    for case in cases:
        for field_name in (
            "id",
            "group",
            "scope",
            "description",
            "invariant",
            "expected",
            "failure_signal",
        ):
            if not getattr(case, field_name).strip():
                problems.append(f"{case.id}: empty {field_name}")

    return problems


def main() -> int:
    cases = build_cases()
    problems = validate(cases)
    if problems:
        print("Context Isolation Benchmark Validation: FAIL")
        for problem in problems:
            print(f"- {problem}")
        return 1

    metadata = benchmark_metadata(cases)
    print(
        "Context Isolation Benchmark Validation: PASS "
        f"({metadata['case_count']} cases, dataset_hash={metadata['dataset_hash']})"
    )
    print("Scope: Dataset/schema only; Runtime implementation is not claimed as complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["main", "validate"]
