"""ModifyRule — verb=MODIFY → resolve → read → llm edit → write."""
from agent.task import Task, Verb, ExecutionPlan, ExecutionStep
from agent.compiler.tool_selector import Rule


class ModifyRule(Rule):
    """MODIFY "runtime.py" → resolve path → read content → llm edit → write."""

    @property
    def verb(self) -> Verb:
        return Verb.MODIFY

    def matches(self, task: Task) -> bool:
        return task.verb == Verb.MODIFY

    def build(self, task: Task, **services) -> ExecutionPlan:
        return ExecutionPlan(
            task=task,
            steps=[
                ExecutionStep(tool="workspace", args={"spec": task.target}, outputs=["path"]),
                ExecutionStep(tool="filesystem.read", args={"path": "$path"}, outputs=["content"]),
                ExecutionStep(
                    tool="llm",
                    args={
                        "verb": "edit",
                        "target": task.target,
                        "content": "$content",
                        "instruction": task.goal,
                        "description": task.description,
                    },
                    outputs=["new_content"],
                ),
                ExecutionStep(tool="filesystem.write", args={"path": "$path", "content": "$new_content"}, outputs=["result"]),
            ],
        )