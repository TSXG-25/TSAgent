"""ReadRule — verb=READ → workspace.resolve + filesystem.read."""
from agent.task import Task, Verb, ExecutionPlan, ExecutionStep
from agent.compiler.tool_selector import Rule


class ReadRule(Rule):
    """READ "runtime.py" → resolve path → read content."""

    @property
    def verb(self) -> Verb:
        return Verb.READ

    def matches(self, task: Task) -> bool:
        return task.verb == Verb.READ

    def build(self, task: Task, **services) -> ExecutionPlan:
        return ExecutionPlan(
            task=task,
            steps=[
                ExecutionStep(tool="workspace", args={"spec": task.target}, outputs=["path"]),
                ExecutionStep(tool="filesystem.read", args={"path": "$path"}, outputs=["content"]),
            ],
        )