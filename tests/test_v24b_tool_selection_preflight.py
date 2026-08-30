from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HARNESS_PATH = ROOT / "realtest_reports" / "harness" / "v24b_tool_selection.py"
SPEC = importlib.util.spec_from_file_location("v24b_tool_selection", HARNESS_PATH)
assert SPEC is not None and SPEC.loader is not None
HARNESS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HARNESS)


def test_preflight_fails_closed_without_production_selector(monkeypatch) -> None:
    monkeypatch.setattr(
        HARNESS,
        "_scan_production_selector",
        lambda: {
            "selector_symbols": [],
            "next_action_construction_sites": [],
            "entry_point_found": False,
            "scan_mode": "test",
        },
    )

    report = HARNESS.build_preflight_report()

    assert report["status"] == "BLOCKED_PRECONDITION"
    assert report["dataset"]["hash_match"] is True
    assert report["dataset"]["case_count"] == 24
    assert report["provider"]["calls"] == 0
    assert report["configuration"]["tool_execution"] is False
    assert len(report["case_reports"]) == 24
    assert {case["failure_category"] for case in report["case_reports"]} == {"P-INT"}
    assert {
        case["failure_subcategory"] for case in report["case_reports"]
    } == {"PRODUCTION_SELECTOR_MISSING"}


def test_current_production_selector_makes_preflight_ready() -> None:
    report = HARNESS.build_preflight_report()

    assert report["status"] == "READY_FOR_REAL_BASELINE"
    assert report["dataset"]["hash_match"] is True
    assert report["provider"]["calls"] == 0
    assert report["case_reports"] == []
    assert {
        symbol["symbol"]
        for symbol in report["production_selector_discovery"]["selector_symbols"]
    } == {"NextActionSelector"}
