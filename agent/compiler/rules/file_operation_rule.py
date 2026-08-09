"""Deterministic file-operation lowering rules.

The Planner may describe a copy/move/delete request, but the Compiler owns the
mapping to the registered filesystem primitives.  The rule deliberately
requires explicit source/destination bindings for copy and move; it does not
infer paths from prose at execution time.
"""

from agent.compiler.tool_selector import Rule
from agent.task import ExecutionPlan, ExecutionStep, Task, Verb


class FileOperationRule(Rule):
    """Lower COPY/MOVE/DELETE into exact filesystem operations."""

    @property
    def verb(self) -> Verb:
        # The Compiler dispatches through matches(); this value is the primary
        # verb for registry/introspection purposes.
        return Verb.COPY

    def matches(self, task: Task) -> bool:
        return task.verb in (Verb.COPY, Verb.MOVE, Verb.DELETE)

    def build(self, task: Task, **services) -> ExecutionPlan:
        if task.verb is Verb.DELETE:
            return ExecutionPlan(
                task=task,
                steps=[
                    ExecutionStep(
                        tool="workspace",
                        args={"spec": task.target, "operation": "source"},
                        outputs=["path"],
                    ),
                    ExecutionStep(
                        tool="filesystem.delete",
                        args={"path": "$path", "exact": True},
                        outputs=["result"],
                    ),
                ],
            )

        source = str((task.inputs or {}).get("source", "")).strip()
        destination = str(
            (task.inputs or {}).get("destination", task.target)
        ).strip()
        if not source or not destination:
            raise ValueError(
                f"{task.verb.value} requires explicit source and destination paths"
            )

        operation = "copy" if task.verb is Verb.COPY else "move"
        return ExecutionPlan(
            task=task,
            steps=[
                ExecutionStep(
                    tool="workspace",
                    args={"spec": source, "operation": "source"},
                    outputs=["source_path"],
                ),
                ExecutionStep(
                    tool="workspace",
                    args={"spec": destination, "operation": "write"},
                    outputs=["destination_path"],
                ),
                ExecutionStep(
                    tool=f"filesystem.{operation}",
                    args={
                        "source": "$source_path",
                        "destination": "$destination_path",
                        "exact": True,
                    },
                    outputs=["result"],
                ),
            ],
        )
