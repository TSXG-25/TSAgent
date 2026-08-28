"""Planner capability acceptance policy.

This module contains the v2.4A measurement contract only.  It does not
change how the production Planner reasons or executes a plan.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PlannerAcceptancePolicy:
    """Frozen thresholds for the first Planner capability gate."""

    minimum_case_count: int = 50
    minimum_schema_validity: float = 1.0
    minimum_dependency_validity: float = 1.0
    minimum_plan_validity: float = 1.0
    maximum_critical_missing_task_rate: float = 0.05
    maximum_overplanning_rate: float = 0.10


DEFAULT_ACCEPTANCE_POLICY = PlannerAcceptancePolicy()


__all__ = ["DEFAULT_ACCEPTANCE_POLICY", "PlannerAcceptancePolicy"]
