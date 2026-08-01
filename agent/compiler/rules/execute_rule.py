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
        # .py 文件需要 python 解释执行，不能直接作为 shell 命令
        if target.endswith(".py"):
            cmd = f"python {target}"
        else:
            cmd = target
        return ExecutionPlan(
            task=task,
            steps=[
                ExecutionStep(
                    tool="shell",
                    args={"cmd": cmd},
                    outputs=["output"],
                ),
            ],
        )