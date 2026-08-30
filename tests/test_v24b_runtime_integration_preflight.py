from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HARNESS_PATH = (
    ROOT / "realtest_reports" / "harness" / "v24b_runtime_integration_preflight.py"
)
SPEC = importlib.util.spec_from_file_location(
    "v24b_runtime_integration_preflight",
    HARNESS_PATH,
)
assert SPEC is not None and SPEC.loader is not None
HARNESS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HARNESS)


def test_current_runtime_integration_fails_closed_at_action_unit_mismatch() -> None:
    report = HARNESS.build_preflight_report()

    assert report["status"] == "BLOCKED_PRECONDITION"
    assert report["configuration"] == {
        "provider_calls": 0,
        "tool_execution": False,
        "runtime_execution": False,
        "source_scan_only": True,
    }
    assert report["discovery"]["selector_consumers"] == []
    assert report["discovery"]["runtime_next_action_passthrough"] is True
    assert report["discovery"]["tool_executor_executes_whole_plan"] is True
    assert report["discovery"]["compiler_has_multi_step_plan"] is True
    assert {blocker["code"] for blocker in report["blockers"]} == {
        "SELECTOR_NOT_CONSUMED_BY_RUNTIME",
        "NEXT_ACTION_STATE_IS_PASSTHROUGH",
        "NEXT_ACTION_EXECUTION_UNIT_MISMATCH",
    }
