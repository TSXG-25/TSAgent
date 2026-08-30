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


def test_runtime_integration_preserves_one_owner_and_one_execution_path() -> None:
    report = HARNESS.build_preflight_report()

    assert report["status"] == "READY_FOR_MIXED_E2E"
    assert report["configuration"] == {
        "provider_calls": 0,
        "tool_execution": False,
        "runtime_execution": False,
        "source_scan_only": True,
    }
    assert {
        item["file"] for item in report["discovery"]["selector_consumers"]
    } == {"agent/orchestrator/executor.py", "agent/orchestrator/main.py"}
    assert report["discovery"]["runtime_next_action_passthrough"] is True
    assert report["discovery"]["tool_executor_executes_whole_plan"] is True
    assert report["discovery"]["compiler_has_multi_step_plan"] is True
    assert report["discovery"]["single_owner_contract"] is True
    assert report["discovery"]["dynamic_action_lowers_to_one_step"] is True
    assert report["discovery"]["both_owners_use_executor_factory"] is True
    assert report["blockers"] == []
