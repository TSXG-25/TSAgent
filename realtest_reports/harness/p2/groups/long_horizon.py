"""P2-L fixture runner for validating the evidence pipeline.

The fixture is intentionally deterministic and clearly marked as such. It is
not a substitute for the later real-provider long-horizon run.
"""
from __future__ import annotations

from benchmarks.p2.cases import P2Case, P2Group, build_cases

from ..evidence import ArtifactEvidence, PerformanceEvidence, RunTraceEvidence
from ..report import LongHorizonResult, make_result


def _long_cases() -> tuple[P2Case, ...]:
    return tuple(case for case in build_cases() if case.group is P2Group.LONG_HORIZON)


def _task_ids(case: P2Case) -> tuple[str, ...]:
    count = 12 if case.id in {"L02", "L04"} else 10
    return tuple(f"task-{index:02d}" for index in range(1, count + 1))


def fixture_result(case: P2Case) -> LongHorizonResult:
    tasks = _task_ids(case)
    artifacts = (
        ArtifactEvidence(
            artifact_id=f"{case.id.lower()}-result",
            digest=f"sha256:{case.id.lower()}-verified",
            verified=True,
            producer="fixture-workflow",
        ),
    )
    transitions = tuple(
        [f"task_started:{task_id}" for task_id in tasks]
        + [f"task_completed:{task_id}" for task_id in tasks]
    )
    replan_count = 1 if case.id == "L03" else 0
    trace = RunTraceEvidence(
        case_id=case.id,
        run_id=f"fixture-{case.id.lower()}",
        provider="fixture",
        planned_tasks=tasks,
        workflow_transitions=transitions,
        task_execution_counts={task_id: 1 for task_id in tasks},
        completed_task_ids=tasks,
        artifacts=artifacts,
        required_artifact_ids=(artifacts[0].artifact_id,),
        terminal_status="COMPLETED",
        terminal_event_type="run_completed",
        terminal_outputs_verified=True,
        performance=PerformanceEvidence(
            wall_ms=100.0 + len(tasks) * 10.0,
            provider_ms=0.0,
            llm_calls=0,
            replans=replan_count,
            tool_calls_count=len(tasks),
            time_to_first_event_ms=1.0,
            time_to_first_artifact_ms=10.0,
        ),
    )
    return make_result(
        case,
        trace,
        capability_outcome="PASS",
        capability_detail="deterministic fixture only; no Provider or Runtime was called",
    )


def run_fixture() -> tuple[LongHorizonResult, ...]:
    """Run each L case exactly once through the evidence/oracle pipeline."""
    return tuple(fixture_result(case) for case in _long_cases())


__all__ = ["fixture_result", "run_fixture"]
