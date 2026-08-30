"""Lower one dynamic Tool action into the canonical execution IR."""

from __future__ import annotations

from agent.next_action import ActionKind, NextAction
from agent.task import ExecutionPlan, ExecutionStep, Task


def lower_dynamic_tool_action(task: Task, action: NextAction) -> ExecutionPlan:
    """Produce a one-step Tool plan without creating a second executor path."""

    if action.kind is not ActionKind.TOOL:
        raise ValueError("DYNAMIC_ACTION_NOT_TOOL")
    if action.task_id != task.id:
        raise ValueError(
            f"DYNAMIC_ACTION_WRONG_TASK: expected {task.id}, got {action.task_id}"
        )
    return ExecutionPlan(
        task=task,
        steps=[ExecutionStep(tool=action.tool, args=dict(action.args), outputs=["result"])],
        executor="tool",
        metadata={"execution_owner": "dynamic"},
    )


__all__ = ["lower_dynamic_tool_action"]
