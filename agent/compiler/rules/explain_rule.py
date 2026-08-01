"""ExplainRule — verb=EXPLAIN → resolve → read → llm summarize."""
from agent.task import Task, Verb, ExecutionPlan, ExecutionStep
from agent.compiler.tool_selector import Rule


class ExplainRule(Rule):
    """EXPLAIN "runtime.py" → resolve path → read content → llm summarize."""

    @property
    def verb(self) -> Verb:
        return Verb.EXPLAIN

    def matches(self, task: Task) -> bool:
        return task.verb == Verb.EXPLAIN

    def build(self, task: Task, **services) -> ExecutionPlan:
        return ExecutionPlan(
            task=task,
            steps=[
                ExecutionStep(tool="workspace", args={"spec": task.target}, outputs=["path"]),
                ExecutionStep(tool="filesystem.read", args={"path": "$path"}, outputs=["content"]),
                ExecutionStep(tool="llm", args={"verb": "summarize", "target": task.target}, outputs=["explanation"]),
            ],
        )