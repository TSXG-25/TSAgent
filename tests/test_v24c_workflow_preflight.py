from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HARNESS_PATH = (
    ROOT / "realtest_reports" / "harness" / "v24c_workflow_preflight.py"
)
SPEC = importlib.util.spec_from_file_location("v24c_workflow_preflight", HARNESS_PATH)
assert SPEC is not None and SPEC.loader is not None
HARNESS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HARNESS)


def test_preflight_fails_closed_before_real_provider_baseline() -> None:
    report = HARNESS.build_preflight_report()

    assert report["status"] == "BLOCKED_PRECONDITION"
    assert report["configuration"] == {
        "provider_calls": 0,
        "workflow_execution": False,
        "runtime_mutation": False,
        "source_scan_only": True,
    }
    assert report["discovery"]["production_selector_symbols"] == []
    assert report["discovery"]["workflow_router_input"] == "IntentResult"
    assert report["discovery"]["planner_hardcoded_workflow_bindings"] is True
    assert report["discovery"]["registered_workflows"] == ["code_generation"]
    assert report["discovery"]["workflow_executor_canonical_task_path"] is True
    assert report["discovery"]["resume_policy_separate"] is True
    assert {item["code"] for item in report["blockers"]} == {
        "PRODUCTION_WORKFLOW_SELECTOR_MISSING",
        "WORKFLOW_INSTANTIATION_BOUNDARY_HARDCODED",
    }
    assert report["preserved_boundaries"] == {
        "workflow_executor_rewrite_required": False,
        "resume_policy_rewrite_required": False,
        "planner_change_required_for_preflight": False,
    }
