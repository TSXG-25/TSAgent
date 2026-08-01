"""WriteRule — verb=WRITE → workspace.resolve + filesystem.write."""
from agent.task import Task, Verb, ExecutionPlan, ExecutionStep
from agent.compiler.tool_selector import Rule


class WriteRule(Rule):
    """WRITE "output.txt" → resolve path → write content."""

    @property
    def verb(self) -> Verb:
        return Verb.WRITE

    def matches(self, task: Task) -> bool:
        return task.verb == Verb.WRITE

    def build(self, task: Task, **services) -> ExecutionPlan:
        return ExecutionPlan(
            task=task,
            steps=[
                ExecutionStep(tool="workspace", args={"spec": task.target}, outputs=["path"]),
                ExecutionStep(tool="filesystem.write", args={"path": "$path"}, outputs=["result"]),
            ],
        )