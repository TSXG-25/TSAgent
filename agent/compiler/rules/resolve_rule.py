"""ResolveRule — verb=RESOLVE → workspace.resolve(target)."""
from agent.task import Task, Verb, ExecutionPlan, ExecutionStep
from agent.compiler.tool_selector import Rule


class ResolveRule(Rule):
    """RESOLVE "runtime" → workspace.resolve(spec="runtime")."""

    @property
    def verb(self) -> Verb:
        return Verb.RESOLVE

    def matches(self, task: Task) -> bool:
        return task.verb == Verb.RESOLVE

    def build(self, task: Task, **services) -> ExecutionPlan:
        return ExecutionPlan(
            task=task,
            steps=[
                ExecutionStep(
                    tool="workspace",
                    args={"spec": task.target},
                    outputs=["matches"],
                ),
            ],
        )