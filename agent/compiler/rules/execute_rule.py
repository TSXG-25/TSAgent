"""ExecuteRule — verb=EXECUTE → shell.execute(command)."""
from agent.task import Task, Verb, ExecutionPlan, ExecutionStep
from agent.compiler.tool_selector import Rule


class ExecuteRule(Rule):
    """EXECUTE "run tests" → shell.execute(command)."""

    @property
    def verb(self) -> Verb:
        return Verb.EXECUTE

    def matches(self, task: Task) -> bool:
        return task.verb == Verb.EXECUTE

    def build(self, task: Task, **services) -> ExecutionPlan:
        target = task.target
        verification_code = str((task.inputs or {}).get("verification_code", "")).strip()
        if verification_code:
            return ExecutionPlan(
                task=task,
                steps=[ExecutionStep(
                    tool="run_python",
                    args={"code": verification_code},
                    outputs=["output"],
                )],
            )
        # Python files use the registered, argument-safe runner rather than a
        # shell command assembled from a model-produced path.
        if target.endswith(".py"):
            return ExecutionPlan(
                task=task,
                steps=[ExecutionStep(
                    tool="run_python_file",
                    args={"path": target},
                    outputs=["output"],
                )],
            )
        return ExecutionPlan(
            task=task,
            steps=[
                ExecutionStep(
                    tool="shell",
                    args={"cmd": target},
                    outputs=["output"],
                ),
            ],
        )
