"""Contract and oracle tests for the v2.2C seed dataset."""
from __future__ import annotations

from benchmarks.run_resume.cases import build_cases
from benchmarks.run_resume.oracle import evaluate
from benchmarks.run_resume.validate import validate


def test_run_resume_dataset_has_sixteen_unique_cases_and_required_groups():
    cases = build_cases()
    assert len(cases) == 16
    assert len({case.id for case in cases}) == 16
    assert {
        "exact_resume", "replay_active_workflow", "cross_workflow_side_effect",
        "upstream_dependency", "run_selection_conflict", "checkpoint_consistency",
        "workflow_version", "process_restart", "resume_completion_evidence",
    }.issubset({case.group for case in cases})


def test_run_resume_dataset_oracle_passes():
    assert validate(build_cases()) == []


def test_process_restart_decision_is_byte_stable_after_round_trip():
    case = next(case for case in build_cases() if case.id == "run-restart-001")
    original = evaluate(case.index, case.request).to_dict()
    restored_index = type(case.index).from_dict(case.index.to_dict())
    restored_request = type(case.request).from_dict(case.request.to_dict())
    assert evaluate(restored_index, restored_request).to_dict() == original


def test_completed_workflow_is_never_selected_for_resume():
    case = next(case for case in build_cases() if case.id == "run-effect-001")
    decision = evaluate(case.index, case.request)
    assert decision.selected_workflow_id == "wf.b"
    assert "wf.a" in decision.skipped_workflow_ids
