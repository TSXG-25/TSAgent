"""Planner output containers.

Planner owns plan-level metadata only.  Task is defined and imported from
``agent.task`` at the boundary, but is not re-exported from this module.
"""
from typing import List

from pydantic import BaseModel, Field

from agent.task import Task as _Task


class PlanMetadata(BaseModel):
    """Planner-only metadata; it is not a second task model."""

    reasoning: str = Field(default="", description="Why the plan was decomposed this way")
    estimated_steps: int = Field(default=0, description="Estimated number of steps")
    constraints: List[str] = Field(
        default_factory=list,
        description="Explicit constraints detected by the Planner",
    )


class TaskList(BaseModel):
    """Structured Planner response containing canonical Tasks."""

    tasks: List[_Task] = Field(description="Decomposed tasks")
    metadata: PlanMetadata = Field(default_factory=PlanMetadata)


__all__ = ["PlanMetadata", "TaskList"]
