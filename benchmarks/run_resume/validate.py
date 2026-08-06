#!/usr/bin/env python3
"""Validate the v2.2C Dataset and deterministic Run-Level Resume Oracle."""
from __future__ import annotations

import json
import sys
from collections import Counter

from .cases import (
    RUN_RESUME_BENCHMARK_VERSION,
    RunResumeDisposition,
    RunResumeCase,
    build_cases,
)
from .metadata import benchmark_metadata
from .oracle import evaluate


def _expected_payload(case: RunResumeCase) -> dict[str, object]:
    expected = case.expected.to_dict()
    expected.pop("post_resume_verifier_status", None)
    return expected


def validate_case(case: RunResumeCase) -> list[str]:
    problems: list[str] = []
    decision = evaluate(case.index, case.request)
    actual = decision.to_dict()
    expected = _expected_payload(case)
    for field in (
        "disposition", "workflow_action", "selected_workflow_id",
        "selected_checkpoint_id", "skipped_workflow_ids", "remaining_workflow_ids",
        "reason_code", "resulting_status",
    ):
        if actual[field] != expected[field]:
            problems.append(
                f"{case.id}: {field} expected={expected[field]!r} actual={actual[field]!r}"
            )

    if case.expected.disposition == RunResumeDisposition.ALLOW:
        if case.expected.workflow_action is None:
            problems.append(f"{case.id}: ALLOW case must declare workflow_action")
        if case.expected.selected_workflow_id != case.index.active_workflow_id:
            problems.append(f"{case.id}: ALLOW must select the active Workflow")
    else:
        if case.expected.workflow_action is not None:
            problems.append(f"{case.id}: blocked case cannot declare workflow_action")
        if case.expected.selected_workflow_id is not None:
            problems.append(f"{case.id}: blocked case cannot select a Workflow")

    if not set(case.expected.must_not_execute_workflow_ids).issubset(
        set(decision.skipped_workflow_ids)
    ):
        problems.append(f"{case.id}: completed Workflow side effects are not protected by skip boundary")

    if case.expected.post_resume_verifier_status is not None:
        if case.expected.post_resume_verifier_status != "VERIFIED":
            problems.append(f"{case.id}: completion evidence must require VERIFIED")
        if not case.oracle_only:
            problems.append(f"{case.id}: post-resume verifier expectation must remain fixture/oracle-only")

    repeated = evaluate(case.index, case.request)
    if json.dumps(actual, sort_keys=True, ensure_ascii=False) != json.dumps(
        repeated.to_dict(), sort_keys=True, ensure_ascii=False
    ):
        problems.append(f"{case.id}: oracle is not deterministic")

    restored_index = type(case.index).from_dict(case.index.to_dict())
    restored_request = type(case.request).from_dict(case.request.to_dict())
    if evaluate(restored_index, restored_request).to_dict() != actual:
        problems.append(f"{case.id}: index/request round-trip changed oracle decision")
    if case.request.rehydrated_from_store and restored_index.to_dict() != case.index.to_dict():
        problems.append(f"{case.id}: rehydrated Run index is not equivalent to stored index")
    return problems


def validate(cases: list[RunResumeCase] | None = None) -> list[str]:
    cases = cases if cases is not None else build_cases()
    problems: list[str] = []
    ids = [case.id for case in cases]
    duplicates = [case_id for case_id, count in Counter(ids).items() if count > 1]
    if duplicates:
        problems.append(f"duplicate case ids: {duplicates}")
    required_groups = {
        "exact_resume", "replay_active_workflow", "cross_workflow_side_effect",
        "upstream_dependency", "run_selection_conflict", "checkpoint_consistency",
        "workflow_version", "process_restart", "resume_completion_evidence",
    }
    actual_groups = {case.group for case in cases}
    missing_groups = sorted(required_groups - actual_groups)
    if missing_groups:
        problems.append(f"missing required groups: {missing_groups}")
    for case in cases:
        problems.extend(validate_case(case))
    return problems


def main() -> int:
    cases = build_cases()
    problems = validate(cases)
    metadata = benchmark_metadata(cases)
    if problems:
        print(f"Run Resume Benchmark Validation: FAIL ({len(problems)} 个评测器缺陷)")
        for problem in problems[:40]:
            print(f"  ✗ {problem}")
        return 1
    oracle_only = sum(1 for case in cases if case.oracle_only)
    print(
        "Run Resume Benchmark Validation: PASS "
        f"[{metadata['benchmark_name']}]（{metadata['case_count']} cases；"
        f"oracle_only={oracle_only}；version={RUN_RESUME_BENCHMARK_VERSION}；"
        f"dataset_hash={metadata['dataset_hash']}）"
    )
    print("Scope: Run-level index/oracle only; Orchestrator E2E reserved for v2.2C implementation phase")
    return 0


if __name__ == "__main__":
    sys.exit(main())
