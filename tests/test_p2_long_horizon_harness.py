"""Offline evidence/oracle/report checks for the P2-L harness slice."""

from benchmarks.p2.cases import P2Group, build_cases
from realtest_reports.harness.p2.evidence import (
    ArtifactEvidence,
    RunTraceEvidence,
)
from realtest_reports.harness.p2.groups.long_horizon import run_fixture
from realtest_reports.harness.p2.invariants import evaluate_runtime_invariants
from realtest_reports.harness.p2.report import build_report


def test_fixture_covers_only_the_five_long_horizon_cases() -> None:
    results = run_fixture()

    assert [result.case.id for result in results] == ["L01", "L02", "L03", "L04", "L05"]
    assert all(result.trace.provider == "fixture" for result in results)
    assert all(result.invariants.runtime_correctness == "PASS" for result in results)
    assert results[2].trace.performance.replans == 1


def test_invariants_catch_false_completed_and_reexecution() -> None:
    trace = RunTraceEvidence(
        case_id="L-test",
        run_id="run-test",
        provider="fixture",
        planned_tasks=("task-1",),
        workflow_transitions=(),
        task_execution_counts={"task-1": 2},
        completed_task_ids=("task-1",),
        artifacts=(),
        required_artifact_ids=("result",),
        terminal_status="COMPLETED",
        terminal_event_type="run_completed",
        terminal_outputs_verified=True,
    )

    invariants = evaluate_runtime_invariants(trace)

    assert invariants.false_completed
    assert invariants.completed_task_reexecutions == 1
    assert invariants.missing_required_artifacts == 1
    assert invariants.runtime_correctness == "FAIL"


def test_evidence_round_trip_and_report_keep_three_layers_separate() -> None:
    case = next(case for case in build_cases() if case.group is P2Group.LONG_HORIZON)
    result = run_fixture()[0]
    restored = RunTraceEvidence.from_dict(result.trace.to_dict())
    report = build_report((result,), source="fixture", commit="test-commit")

    assert restored == result.trace
    assert report["source"] == "fixture"
    assert report["results"][0]["evidence"]["provider"] == "fixture"
    assert report["results"][0]["oracle"]["case_id"] == case.id
    assert report["results"][0]["runtime"]["runtime_correctness"] == "PASS"
    assert report["results"][0]["capability"]["outcome"] == "PASS"


def test_verified_artifact_is_not_missing() -> None:
    trace = RunTraceEvidence(
        case_id="L-artifact",
        run_id="run-artifact",
        provider="fixture",
        planned_tasks=(),
        workflow_transitions=(),
        task_execution_counts={},
        completed_task_ids=(),
        artifacts=(ArtifactEvidence("result", "sha256:1", True),),
        required_artifact_ids=("result",),
        terminal_status="COMPLETED",
        terminal_event_type="run_completed",
        terminal_outputs_verified=True,
    )

    invariants = evaluate_runtime_invariants(trace)

    assert invariants.missing_required_artifacts == 0
    assert not invariants.false_completed


def test_provider_errors_are_preserved_as_stable_case_evidence() -> None:
    trace = RunTraceEvidence.from_dict(
        {
            "case_id": "L-provider",
            "run_id": "run-provider",
            "provider": "primary",
            "provider_errors": ["TimeoutError", "ConnectionError"],
        }
    )

    assert trace.provider_errors == ("TimeoutError", "ConnectionError")
    assert "provider_errors" in trace.to_dict()
