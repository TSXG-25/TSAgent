"""Deterministic contract-calibration tests for v2.4A-2c."""

from __future__ import annotations

import copy
from collections import Counter

from evals.planner.dataset_v1_1 import (
    dataset_hash_v1_1,
    load_dataset_v1_1,
    planner_cases_v1_1,
    routing_cases_v1_1,
    validate_dataset_v1_1,
)
from evals.planner.oracle import (
    aggregate_metrics as aggregate_planner_metrics,
    dataset_hash,
    evaluate_golden,
    evaluate_plan,
    golden_plan,
    load_dataset,
    validate_dataset,
)
from evals.planner.report import acceptance_gate
from evals.routing.oracle import (
    dataset_hash as routing_dataset_hash,
    evaluate_route,
    golden_route,
    load_dataset as load_routing_dataset,
    validate_dataset as validate_routing_dataset,
)
from evals.uncertainty.oracle import (
    aggregate_metrics as aggregate_uncertainty_metrics,
    dataset_hash as uncertainty_dataset_hash,
    evaluate_decision,
    load_dataset as load_uncertainty_dataset,
    validate_dataset as validate_uncertainty_dataset,
)
from realtest_reports.harness.v24a_planner_rebaseline import (
    _reclassify_uncertainty_records,
)


V1_HASH = "7f5b28f608194a324f4244c860a8ed9101bcb7afa3b68e5129632ebfb0290291"
V1_1_HASH = "8c268b5855d109c7a2be940257ae0acf7edc877793dd5914cc020ae380aae023"
ROUTING_HASH = "f3aea7b4cecdcd1997a7716f9c3e7b2396efa2ff6fb3e6b1721784135d345458"
UNCERTAINTY_HASH = "8f1479bdded0f00e20fd4d283082869d078b4fa217719dc768ae8a6afaaf1cdb"


def test_frozen_v1_baseline_is_unchanged() -> None:
    payload = load_dataset()

    assert validate_dataset(payload).valid
    assert dataset_hash(payload) == V1_HASH
    reports, metrics = evaluate_golden(payload)
    assert len(reports) == 50
    assert metrics.executable_plan_rate == 1.0
    assert all(report["passed"] for report in reports)


def test_v1_1_owns_chat_cases_outside_planner() -> None:
    payload = load_dataset_v1_1()
    valid, errors = validate_dataset_v1_1(payload)

    assert valid, errors
    assert dataset_hash_v1_1(payload) == V1_1_HASH
    assert len(payload["cases"]) == 50
    assert len(planner_cases_v1_1(payload)) == 46
    assert len(routing_cases_v1_1(payload)) == 4
    assert {case["id"] for case in routing_cases_v1_1(payload)} == {
        "PA001",
        "PA002",
        "PA003",
        "PA004",
    }
    assert all(case["ownership"] == "planner" for case in planner_cases_v1_1(payload))
    assert all(case["ownership"] == "routing" for case in routing_cases_v1_1(payload))

    planner_reports = [
        evaluate_plan(case, golden_plan(case))
        for case in planner_cases_v1_1(payload)
    ]
    planner_metrics = aggregate_planner_metrics(planner_reports)
    assert planner_metrics.case_count == 46
    assert acceptance_gate(planner_metrics)["case_count"] is True


def test_v1_1_aliases_extend_only_explicit_text_targets() -> None:
    v1 = load_dataset()
    v1_1 = load_dataset_v1_1()
    v1_case = next(case for case in v1["cases"] if case["id"] == "PA042")
    v1_1_case = next(case for case in v1_1["cases"] if case["id"] == "PA042")
    alias = v1_1_case["goal_units"][0]["target_aliases"][0]
    assert v1_case["goal_units"][0].get("target_aliases") is None
    assert v1_1_case["goal_units"][0]["target_type"] == "text"

    alias_plan = golden_plan(v1_1_case)
    alias_plan["tasks"][0]["target"] = alias
    assert evaluate_plan(v1_1_case, alias_plan)["passed"] is True
    assert evaluate_plan(v1_case, alias_plan)["passed"] is False


def test_routing_dataset_is_independent_and_deterministic() -> None:
    payload = load_routing_dataset()

    assert not validate_routing_dataset(payload)
    assert routing_dataset_hash(payload) == ROUTING_HASH
    reports = [evaluate_route(case, golden_route(case)) for case in payload["cases"]]
    assert len(reports) == 4
    assert all(report["passed"] for report in reports)
    assert all(report["planner_called"] is False for report in reports)


def test_uncertainty_dataset_has_paired_boundaries_and_stable_oracle() -> None:
    payload = load_uncertainty_dataset()

    assert not validate_uncertainty_dataset(payload)
    assert uncertainty_dataset_hash(payload) == UNCERTAINTY_HASH
    assert 20 <= len(payload["cases"]) <= 30
    assert min(Counter(case["pair_id"] for case in payload["cases"]).values()) >= 2

    reports = [
        evaluate_decision(case, bool(case["expected_abstain"]))
        for case in payload["cases"]
    ]
    metrics = aggregate_uncertainty_metrics(reports)
    assert metrics["case_count"] == 27
    assert metrics["pass_count"] == 27
    assert metrics["true_abstain"] == 12
    assert metrics["true_proceed"] == 15


def test_calibration_hashes_survive_deep_copy() -> None:
    for loader, hasher in (
        (load_dataset_v1_1, dataset_hash_v1_1),
        (load_routing_dataset, routing_dataset_hash),
        (load_uncertainty_dataset, uncertainty_dataset_hash),
    ):
        payload = loader()
        assert hasher(payload) == hasher(copy.deepcopy(payload))


def test_rebaseline_excludes_pre_planner_abstention_from_capability() -> None:
    records = [
        {
            "case_id": "PA022",
            "expected_mode": "plan",
            "provider_status": "NOT_CALLED",
            "provider_calls": [],
            "planner_output": {"abstain": True},
            "oracle_result": {"passed": False},
            "failure_category": "P-CAP",
            "failure_subcategory": "FALSE_CLARIFICATION",
            "evaluable": True,
            "passed": False,
        },
        {
            "case_id": "PA016",
            "expected_mode": "plan",
            "provider_status": "SUCCESS",
            "provider_calls": [{"request_id": "req-1"}],
            "planner_output": {"abstain": False},
            "oracle_result": {"passed": False},
            "failure_category": "P-CAP",
            "failure_subcategory": "UNDER_PLAN",
            "evaluable": True,
            "passed": False,
        },
    ]

    calibrated, reclassified = _reclassify_uncertainty_records(records)

    assert reclassified == ["PA022"]
    assert calibrated[0]["failure_category"] == "P-UNCERTAINTY"
    assert calibrated[0]["failure_subcategory"] == "FALSE_ABSTENTION"
    assert calibrated[0]["evaluable"] is False
    assert calibrated[0]["excluded_from_capability"] is True
    assert calibrated[1]["failure_category"] == "P-CAP"
    assert records[0]["failure_category"] == "P-CAP"
