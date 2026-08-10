from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from realtest_reports.harness.p2.groups.restart import (
    CRASH_POINTS,
    EXPECTED_EFFECTS,
    _evaluate_case,
    _marker_valid,
)
from realtest_reports.harness.p2.groups.restart_worker import (
    build_index,
    build_workflows,
)


@pytest.mark.parametrize("case_id", tuple(CRASH_POINTS))
def test_restart_manifest_builds_deterministic_workflows(case_id: str) -> None:
    workflows = build_workflows(case_id)
    index = build_index(
        case_id,
        run_id=f"run-{case_id.lower()}",
        session_id="session-p2r",
        user_id="user-p2r",
    )

    assert tuple(workflows) == index.workflow_sequence
    assert index.pending_workflow_ids == index.workflow_sequence
    assert not index.active_workflow_id
    assert {
        stage.id
        for workflow in workflows.values()
        for stage in workflow.stages
    } == set(EXPECTED_EFFECTS[case_id])


def test_r01_marker_requires_durable_completed_stage() -> None:
    marker = {"point": "after_run_active"}
    valid = {
        "run_index": {"active_workflow_id": "wf-main"},
        "checkpoints": [{"completed_task_ids": ["r01-stage-1"]}],
        "workspace_files": {},
    }
    invalid = {
        **valid,
        "checkpoints": [{"completed_task_ids": []}],
    }

    assert _marker_valid("R01", marker, valid)
    assert not _marker_valid("R01", marker, invalid)


def test_restart_gate_rejects_event_gap_and_false_completion(tmp_path: Path) -> None:
    marker = {"point": "after_checkpoint_commit"}
    checkpoint = {
        "checkpoint_id": "cp-r03",
        "completed_task_ids": ["r03-event-write"],
    }
    index = {
        "workflow_sequence": ["wf-event"],
        "completed_workflow_ids": ["wf-event"],
        "active_workflow_id": "",
        "pending_workflow_ids": [],
    }
    pre = {
        "run_index": index,
        "checkpoints": [checkpoint],
        "workspace_files": {},
    }
    post = {
        "head": {"run_status": "COMPLETED", "current_revision": 4},
        "run_index": index,
        "checkpoints": [checkpoint],
        "events": [
            {
                "sequence_number": 1,
                "event_id": "created",
                "event_type": "run_created",
                "run_revision": 1,
            },
            {
                "sequence_number": 3,
                "event_id": "completed",
                "event_type": "run_completed",
                "run_revision": 4,
            },
        ],
        "workspace_files": {},
    }
    gates, diagnostics = _evaluate_case(
        "R03",
        marker=marker,
        pre=pre,
        post=post,
        audit_counts=Counter({"r03-event-write": 1}),
        stale_probe={"attempted": True, "accepted": False, "code": "STALE_WRITER"},
        worker_a_returncode=-9,
        worker_b_returncode=0,
        legacy_root=tmp_path / "legacy-output",
    )

    assert diagnostics["event_sequences"] == [1, 3]
    assert not gates["event_replay_gap_zero"]
    assert not gates["false_completed_zero"]
