"""Deterministic contract tests for the v2.4A Planner benchmark."""

from __future__ import annotations

import copy

from evals.planner.oracle import (
    dataset_hash,
    evaluate_golden,
    evaluate_plan,
    golden_plan,
    load_dataset,
    validate_dataset,
)
from evals.planner.report import acceptance_gate


def test_v24a_dataset_is_frozen_and_complete() -> None:
    payload = load_dataset()

    assert validate_dataset(payload).valid
    assert payload["version"] == "v2.4A-planner-v1"
    assert len(payload["cases"]) == 50
    assert {case["family"] for case in payload["cases"]} == {
        f"P{number}" for number in range(1, 13)
    }
    assert dataset_hash(payload) == dataset_hash(copy.deepcopy(payload))


def test_v24a_golden_oracle_passes_all_cases() -> None:
    payload = load_dataset()
    reports, metrics = evaluate_golden(payload)

    assert len(reports) == 50
    assert metrics.case_count == 50
    assert metrics.schema_validity == 1.0
    assert metrics.dependency_validity == 1.0
    assert metrics.plan_validity == 1.0
    assert metrics.dependency_accuracy == 1.0
    assert metrics.task_granularity == 1.0
    assert metrics.executable_plan_rate == 1.0
    assert metrics.critical_missing_task_rate == 0.0
    assert metrics.overplanning_rate == 0.0
    assert not [report for report in reports if not report["passed"]]
    assert all(acceptance_gate(metrics).values())


def test_parallel_case_preserves_independent_branches() -> None:
    payload = load_dataset()
    case = next(item for item in payload["cases"] if item["id"] == "PA041")
    report = evaluate_plan(case, golden_plan(case))

    assert report["passed"] is True
    assert report["matched_goal_units"] == {
        "g1": "task-1",
        "g2": "task-2",
        "g3": "task-3",
    }
    assert report["dependency_accuracy"] == 1.0
    assert golden_plan(case)["tasks"][2]["dependencies"] == ["task-1", "task-2"]


def test_resume_case_omits_completed_units() -> None:
    payload = load_dataset()
    case = next(item for item in payload["cases"] if item["id"] == "PA046")
    plan = golden_plan(case)

    assert [task["target"] for task in plan["tasks"]] == ["output/summary.md"]
    assert plan["tasks"][0]["dependencies"] == []
    assert evaluate_plan(case, plan)["passed"] is True


def test_oracle_rejects_unknown_dependency() -> None:
    payload = load_dataset()
    case = next(item for item in payload["cases"] if item["id"] == "PA005")
    plan = golden_plan(case)
    plan["tasks"][0]["dependencies"] = ["task-missing"]

    report = evaluate_plan(case, plan)

    assert report["schema_validity"] == 1.0
    assert report["dependency_validity"] == 0.0
    assert report["plan_validity"] == 0.0
    assert report["passed"] is False


def test_oracle_rejects_duplicate_task_ids() -> None:
    payload = load_dataset()
    case = next(item for item in payload["cases"] if item["id"] == "PA041")
    plan = golden_plan(case)
    plan["tasks"][1]["id"] = plan["tasks"][0]["id"]

    report = evaluate_plan(case, plan)

    assert report["schema_validity"] == 0.0
    assert report["dependency_validity"] == 0.0
    assert report["plan_validity"] == 0.0
    assert report["passed"] is False


def test_oracle_measures_extra_task_as_overplanning() -> None:
    payload = load_dataset()
    case = next(item for item in payload["cases"] if item["id"] == "PA005")
    plan = golden_plan(case)
    extra = copy.deepcopy(plan["tasks"][0])
    extra["id"] = "task-extra"
    extra["verb"] = "explain"
    extra["target"] = "额外说明"
    extra["target_type"] = "text"
    extra["dependencies"] = []
    plan["tasks"].append(extra)

    report = evaluate_plan(case, plan)

    assert report["unexpected_task_ids"] == ["task-extra"]
    assert report["unnecessary_task_count"] == 1
    assert report["overplanned"] == 1.0
