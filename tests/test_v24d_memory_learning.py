from __future__ import annotations

import importlib.util
from pathlib import Path

from evals.memory_learning.oracle import (
    dataset_hash,
    evaluate_decision,
    golden_decision,
    golden_self_check,
    load_dataset,
    validate_dataset,
)


ROOT = Path(__file__).resolve().parents[1]
HARNESS_PATH = ROOT / "realtest_reports" / "harness" / "v24d_memory_learning_preflight.py"
SPEC = importlib.util.spec_from_file_location("v24d_memory_learning_preflight", HARNESS_PATH)
assert SPEC is not None and SPEC.loader is not None
HARNESS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HARNESS)


def test_memory_learning_dataset_is_frozen_and_valid() -> None:
    payload = load_dataset()

    assert validate_dataset(payload) == ()
    assert len(payload["cases"]) == 24
    assert dataset_hash(payload) == "e821b67e7da66b40a7c1cc38ac6e18d1636b6d01c6aeaa3e873f03bb95f928b7"
    assert {case["family"] for case in payload["cases"]} == {
        "ELIGIBILITY",
        "SOURCE_AUTHORITY",
        "SCOPE",
        "DEDUP_CONFLICT",
        "SENSITIVITY_VOLATILITY",
        "LIFECYCLE_BOUNDARY",
    }


def test_golden_memory_learning_contract_is_24_of_24_without_safety_violations() -> None:
    assert golden_self_check() == {
        "case_count": 24,
        "pass_count": 24,
        "schema_validity": 24,
        "decision_accuracy": 24,
        "safe_decision_rate": 24,
        "false_memory_write_count": 0,
        "unprovenanced_write_count": 0,
        "scope_violation_count": 0,
        "sensitive_write_count": 0,
        "volatile_write_count": 0,
        "duplicate_write_count": 0,
    }


def test_oracle_rejects_a_false_memory_write() -> None:
    case = next(case for case in load_dataset()["cases"] if case["id"] == "D007")
    decision = {
        "action": "STORE",
        "memory_type": "fact",
        "scope": "user",
        "canonical_key": "market.btc_price",
        "value": "100000 USD",
        "provenance": {
            "evidence_id": "ev-007",
            "source_kind": "tool_observation",
            "source_ref": "tool:market:7",
        },
        "reason_code": "store_anyway",
    }

    report = evaluate_decision(case, decision)

    assert report["passed"] is False
    assert report["volatile_write"] == 1
    assert report["unprovenanced_write"] == 1


def test_preflight_fails_closed_without_production_learning_entry() -> None:
    report = HARNESS.build_preflight_report()

    assert report["status"] == "BLOCKED_PRECONDITION"
    assert report["configuration"] == {
        "provider_calls": 0,
        "memory_writes": 0,
        "store_imports": 0,
        "source_scan_only": True,
    }
    assert report["case_reports"]
    assert {item["failure_category"] for item in report["case_reports"]} == {"P-INT"}
    assert "PRODUCTION_MEMORY_LEARNING_ENTRY_MISSING" in {
        item["code"] for item in report["blockers"]
    }
    assert report["discovery"]["retrieval_scope_fallback_without_filter"] is True
    assert report["preserved_boundaries"]["memory_store_modified"] is False


def test_golden_decision_is_contract_data_only() -> None:
    case = load_dataset()["cases"][0]
    assert golden_decision(case) == case["expected"]
