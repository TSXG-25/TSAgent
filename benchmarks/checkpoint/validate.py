#!/usr/bin/env python3
"""v2.2A Checkpoint Dataset / Oracle Validation.

This command validates the schema and pure decision oracle.  It does not run
Workflow, Planner, Executor, external tools, or real-world resume scenarios.
Those are explicitly v2.2B/C responsibilities.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

from agent.checkpoint import (
    CheckpointStatus,
    ResumeContext,
    RunCheckpoint,
    ResumeAction,
    ResumeDisposition,
    ResumeReasonCode,
    deserialize_checkpoint,
    lifecycle_contract,
    project_pending_target,
    serialize_checkpoint,
    validate_resume,
)
from benchmarks.checkpoint.cases import (
    CHECKPOINT_BENCHMARK_VERSION,
    build_cases,
    build_registry,
)
from benchmarks.checkpoint.metadata import benchmark_metadata


def _validate_case(case) -> list[str]:
    problems: list[str] = []
    try:
        # Use the model directly so a schema-incompatibility case can reach
        # ResumeValidator instead of being rejected by the current codec.
        checkpoint = RunCheckpoint.from_dict(case.checkpoint)
        context = ResumeContext.from_dict(case.context)
        registry = build_registry(case.registry)
        decision = validate_resume(checkpoint, context, compatibility_registry=registry)
    except Exception as exc:  # dataset defect, not an Agent result
        return [f"{case.id}: fixture cannot be evaluated: {type(exc).__name__}: {exc}"]

    actual = decision.to_dict()
    expected = {
        "disposition": case.expected_disposition,
        "action": case.expected_action,
        "reason_code": case.expected_reason,
        "resulting_status": case.expected_resulting_status,
    }
    for field, value in expected.items():
        if actual[field] != value:
            problems.append(
                f"{case.id}: {field} expected={value!r} actual={actual[field]!r}"
            )

    if case.expected_disposition == ResumeDisposition.ALLOW.value and not case.expected_action:
        problems.append(f"{case.id}: ALLOW fixture must declare expected_action")
    if case.expected_disposition != ResumeDisposition.ALLOW.value and case.expected_action:
        problems.append(f"{case.id}: non-ALLOW fixture cannot declare expected_action")
    if case.expected_disposition == ResumeDisposition.REQUIRE_CLARIFICATION.value:
        if not decision.clarification_question:
            problems.append(f"{case.id}: clarification must include a question")

    # Codec and Validator must agree after a round trip.  The expected schema
    # is the fixture's own schema so incompatible fixtures can still be read
    # as historical facts for validation.
    try:
        restored = deserialize_checkpoint(
            serialize_checkpoint(checkpoint),
            expected_schema_version=checkpoint.checkpoint_schema_version,
        )
        restored_decision = validate_resume(
            restored,
            context,
            compatibility_registry=registry,
        )
        if decision.to_dict() != restored_decision.to_dict():
            problems.append(f"{case.id}: codec round-trip changed ResumeDecision")
    except Exception as exc:
        problems.append(f"{case.id}: codec round-trip failed: {type(exc).__name__}: {exc}")

    # Same immutable inputs must produce byte-equivalent decision payloads.
    repeated = validate_resume(checkpoint, context, compatibility_registry=registry)
    if json.dumps(decision.to_dict(), sort_keys=True, ensure_ascii=False) != json.dumps(
        repeated.to_dict(), sort_keys=True, ensure_ascii=False
    ):
        problems.append(f"{case.id}: ResumeDecision is not deterministic")
    return problems


def _validate_projection() -> list[str]:
    problems: list[str] = []
    case = build_cases()[0]
    checkpoint = RunCheckpoint.from_dict(case.checkpoint)
    pending = project_pending_target(checkpoint)
    if pending is None:
        return ["projection: active checkpoint unexpectedly produced no PendingTarget"]
    payload = pending.to_dict()
    expected_keys = {
        "run_id", "workflow_id", "target_summary", "active_stage_summary",
        "status", "last_updated_at",
    }
    if set(payload) != expected_keys:
        problems.append(f"projection: schema drift {sorted(payload)}")
    forbidden = {"checkpoint_id", "parent_checkpoint_id", "execution_plan"}
    if forbidden.intersection(payload):
        problems.append("projection: PendingTarget leaks checkpoint reconstruction fields")
    if hasattr(type(pending), "from_pending_target"):
        problems.append("projection: reverse reconstruction API must not exist")
    terminal = RunCheckpoint.from_dict({
        **case.checkpoint,
        "status": CheckpointStatus.COMPLETED.value,
    })
    if project_pending_target(terminal) is not None:
        problems.append("projection: terminal checkpoint projected as pending")
    return problems


def _validate_lifecycle() -> list[str]:
    contract = lifecycle_contract()
    expected_states = {status.value for status in CheckpointStatus}
    problems = []
    if set(contract) != expected_states:
        problems.append("lifecycle: status set drift")
    for terminal in ("COMPLETED", "CANCELLED", "FAILED_TERMINAL"):
        if contract.get(terminal):
            problems.append(f"lifecycle: terminal status {terminal} has outgoing transitions")
    return problems


def main() -> int:
    cases = build_cases()
    problems: list[str] = []
    ids = [case.id for case in cases]
    duplicates = [case_id for case_id, count in Counter(ids).items() if count > 1]
    if duplicates:
        problems.append(f"duplicate case ids: {duplicates}")
    for case in cases:
        problems.extend(_validate_case(case))
    problems.extend(_validate_projection())
    problems.extend(_validate_lifecycle())

    metadata = benchmark_metadata(cases)
    if problems:
        print(f"Checkpoint Benchmark Validation: FAIL ({len(problems)} 个评测器缺陷)")
        for problem in problems[:40]:
            print(f"  ✗ {problem}")
        return 1

    counts = Counter((case.group, case.oracle_only) for case in cases)
    pure = sum(count for (group, oracle), count in counts.items() if not oracle)
    oracle = sum(count for (group, oracle), count in counts.items() if oracle)
    print(
        "Checkpoint Benchmark Validation: PASS "
        f"[{metadata['benchmark_name']}]（{metadata['case_count']} cases；"
        f"pure={pure}；fixture_oracle={oracle}；"
        f"version={CHECKPOINT_BENCHMARK_VERSION}；"
        f"dataset_hash={metadata['dataset_hash']}）"
    )
    print("Scope: schema/oracle/codec/validator only; Workflow E2E reserved for v2.2B/C")
    return 0


if __name__ == "__main__":
    sys.exit(main())
