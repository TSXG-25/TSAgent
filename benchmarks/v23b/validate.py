#!/usr/bin/env python3
"""Validate the v2.3B transaction/crash Dataset and pure oracle."""
from __future__ import annotations

import json
import sys
from collections import Counter

from .cases import (
    CrashTrigger,
    EffectState,
    OracleOutcome,
    StoreCrashCase,
    build_cases,
)
from .metadata import benchmark_metadata
from .oracle import evaluate


REQUIRED_GROUPS = frozenset(
    {
        "schema",
        "atomicity",
        "preparation",
        "idempotency",
        "cas",
        "fencing",
        "external_effect",
        "recovery",
        "read_consistency",
    }
)


def validate_case(case: StoreCrashCase) -> list[str]:
    problems: list[str] = []
    for field_name in ("id", "group", "description", "invariant"):
        if not getattr(case, field_name).strip():
            problems.append(f"{case.id}: empty {field_name}")

    decision = evaluate(case)
    if decision.outcome is not case.expected_outcome:
        problems.append(
            f"{case.id}: outcome expected={case.expected_outcome.value} "
            f"actual={decision.outcome.value}"
        )
    if decision.visible_state is not case.expected_visible_state:
        problems.append(
            f"{case.id}: visible state expected={case.expected_visible_state.value} "
            f"actual={decision.visible_state.value}"
        )
    if not case.oracle_only:
        problems.append(f"{case.id}: v2.3B pre-implementation cases must be oracle_only")
    if case.effect_state:
        try:
            EffectState(case.effect_state)
        except ValueError:
            problems.append(f"{case.id}: invalid effect_state={case.effect_state!r}")

    restored = StoreCrashCase.from_dict(case.to_dict())
    if restored.to_dict() != case.to_dict():
        problems.append(f"{case.id}: case round-trip changed contract data")
    repeated = evaluate(restored)
    if json.dumps(decision.to_dict(), sort_keys=True) != json.dumps(
        repeated.to_dict(), sort_keys=True
    ):
        problems.append(f"{case.id}: oracle is not deterministic")
    return problems


def validate(cases: tuple[StoreCrashCase, ...] | None = None) -> list[str]:
    cases = cases if cases is not None else build_cases()
    problems: list[str] = []
    if len(cases) != 19:
        problems.append(f"expected 19 cases, got {len(cases)}")

    ids = [case.id for case in cases]
    duplicates = sorted(case_id for case_id, count in Counter(ids).items() if count > 1)
    if duplicates:
        problems.append(f"duplicate case ids: {', '.join(duplicates)}")

    missing_groups = sorted(REQUIRED_GROUPS - {case.group for case in cases})
    if missing_groups:
        problems.append(f"missing required groups: {', '.join(missing_groups)}")

    if sum(case.trigger in {
        CrashTrigger.PREPARATION_BEFORE_COMMIT,
        CrashTrigger.AFTER_CHECKPOINT_INSERT,
        CrashTrigger.AFTER_ARTIFACT_METADATA,
        CrashTrigger.AFTER_INDEX_UPDATE,
        CrashTrigger.BEFORE_COMMIT,
    } for case in cases) < 4:
        problems.append("atomicity coverage must include all pre-commit crash windows")
    if not any(case.expected_outcome is OracleOutcome.IDEMPOTENT_RETRY for case in cases):
        problems.append("missing after-commit idempotent retry case")
    if not any(case.expected_outcome is OracleOutcome.IDEMPOTENCY_CONFLICT for case in cases):
        problems.append("missing same-key different-digest conflict case")
    if not any(case.trigger is CrashTrigger.DIFFERENT_KEY for case in cases):
        problems.append("missing different-key independent operation case")
    if not any(case.expected_outcome is OracleOutcome.FENCE_ACQUIRED for case in cases):
        problems.append("missing monotonic fence takeover case")
    if not any(case.expected_outcome is OracleOutcome.REJECTED for case in cases):
        problems.append("missing CAS/fencing rejection case")
    if not any(case.expected_outcome is OracleOutcome.REQUIRE_CLARIFICATION for case in cases):
        problems.append("missing unknown external result blocking case")
    if not any(case.expected_outcome is OracleOutcome.RECOVERED for case in cases):
        problems.append("missing process restart recovery case")

    for case in cases:
        problems.extend(validate_case(case))
    return problems


def main() -> int:
    cases = build_cases()
    problems = validate(cases)
    metadata = benchmark_metadata(cases)
    if problems:
        print(f"Durable Store Benchmark Validation: FAIL ({len(problems)} issues)")
        for problem in problems[:40]:
            print(f"  ✗ {problem}")
        return 1
    print(
        "Durable Store Benchmark Validation: PASS "
        f"({metadata['case_count']} cases, dataset_hash={metadata['dataset_hash']})"
    )
    print("Scope: transaction/crash contract and oracle only; SQLite production implementation is pending")
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["main", "validate", "validate_case"]
