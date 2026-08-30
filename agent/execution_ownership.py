"""Single-owner contract for Task execution.

Compiler-owned Tasks execute one complete ``ExecutionPlan``. Explicitly
dynamic Tasks select one ``NextAction`` at a time. Ownership is resolved before
the first Task effect and is immutable for the logical Run.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, model_validator


class ExecutionOwner(str, Enum):
    COMPILED = "compiled"
    DYNAMIC = "dynamic"


class TaskExecutionOwnership(BaseModel):
    """Trusted Runtime-composition input; never Planner output."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    owner: ExecutionOwner = ExecutionOwner.COMPILED
    available_tools: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _validate_owner_fields(self) -> "TaskExecutionOwnership":
        if self.owner is ExecutionOwner.DYNAMIC and not self.available_tools:
            raise ValueError("DYNAMIC_EXECUTION_REQUIRES_AVAILABLE_TOOLS")
        if self.owner is ExecutionOwner.COMPILED and self.available_tools:
            raise ValueError("COMPILED_EXECUTION_CANNOT_DECLARE_DYNAMIC_TOOLS")
        return self


def configure_dynamic_execution(
    state: dict[str, Any],
    task_id: str,
    available_tools: Sequence[str],
) -> None:
    """Declare dynamic ownership before Runtime resolves Task owners."""

    if state.get("resolved_execution_ownership") is not None:
        raise ValueError("EXECUTION_OWNER_IMMUTABLE")
    configured = dict(state.get("execution_ownership") or {})
    if task_id in configured:
        raise ValueError(f"EXECUTION_OWNER_ALREADY_CONFIGURED: {task_id}")
    configured[task_id] = TaskExecutionOwnership(
        owner=ExecutionOwner.DYNAMIC,
        available_tools=tuple(available_tools),
    ).model_dump(mode="json")
    state["execution_ownership"] = configured


def resolve_execution_ownership(
    state: dict[str, Any],
    tasks: Sequence[Mapping[str, Any]],
) -> dict[str, TaskExecutionOwnership]:
    """Resolve exactly one owner per Task and freeze it in Runtime state."""

    configured = dict(state.get("execution_ownership") or {})
    resolved: dict[str, TaskExecutionOwnership] = {}
    for task in tasks:
        task_id = str(task.get("id", ""))
        if not task_id:
            raise ValueError("EXECUTION_OWNER_TASK_ID_MISSING")
        payload = configured.get(task_id, {"owner": ExecutionOwner.COMPILED.value})
        resolved[task_id] = TaskExecutionOwnership.model_validate(payload)

    unknown = set(configured) - set(resolved)
    if unknown:
        raise ValueError(
            "EXECUTION_OWNER_UNKNOWN_TASK: " + ", ".join(sorted(unknown))
        )

    serialized = {
        task_id: ownership.model_dump(mode="json")
        for task_id, ownership in resolved.items()
    }
    snapshot = state.get("resolved_execution_ownership")
    if snapshot is not None and snapshot != serialized:
        raise ValueError("EXECUTION_OWNER_IMMUTABLE")
    state["execution_ownership"] = {
        task_id: dict(payload) for task_id, payload in serialized.items()
    }
    state["resolved_execution_ownership"] = {
        task_id: dict(payload) for task_id, payload in serialized.items()
    }
    return resolved


__all__ = [
    "ExecutionOwner",
    "TaskExecutionOwnership",
    "configure_dynamic_execution",
    "resolve_execution_ownership",
]
