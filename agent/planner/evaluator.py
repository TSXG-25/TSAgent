"""Provider-independent Planner capability metrics.

The evaluator consumes already-normalized per-case Oracle records.  It is
deliberately unaware of LLMs, tools, workspaces, and Runtime state so a
benchmark cannot silently turn a model outcome into a Runtime assertion.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class PlannerMetrics:
    """Aggregate metrics for the v2.4A Planner Dataset."""

    case_count: int
    schema_validity: float
    dependency_validity: float
    plan_validity: float
    dependency_accuracy: float
    task_granularity: float
    unnecessary_task_rate: float
    missing_task_rate: float
    executable_plan_rate: float
    overplanning_rate: float
    critical_missing_task_rate: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def aggregate_metrics(reports: Iterable[Mapping[str, Any]]) -> PlannerMetrics:
    """Aggregate Oracle fields without applying acceptance thresholds."""

    values = list(reports)
    count = len(values)
    if count == 0:
        return PlannerMetrics(
            0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
        )

    def average(name: str) -> float:
        return sum(float(report.get(name, 0.0)) for report in values) / count

    predicted_tasks = sum(int(report.get("predicted_task_count", 0)) for report in values)
    unnecessary_tasks = sum(int(report.get("unnecessary_task_count", 0)) for report in values)
    goal_units = sum(int(report.get("goal_unit_count", 0)) for report in values)
    missing_tasks = sum(int(report.get("missing_task_count", 0)) for report in values)

    return PlannerMetrics(
        case_count=count,
        schema_validity=average("schema_validity"),
        dependency_validity=average("dependency_validity"),
        plan_validity=average("plan_validity"),
        dependency_accuracy=average("dependency_accuracy"),
        task_granularity=average("task_granularity"),
        unnecessary_task_rate=(
            unnecessary_tasks / predicted_tasks if predicted_tasks else 0.0
        ),
        missing_task_rate=missing_tasks / goal_units if goal_units else 0.0,
        executable_plan_rate=average("executable_plan"),
        overplanning_rate=average("overplanned"),
        critical_missing_task_rate=average("critical_missing"),
    )


__all__ = ["PlannerMetrics", "aggregate_metrics"]
