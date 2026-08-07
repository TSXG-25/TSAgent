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
        steps = [
            ExecutionStep(tool="workspace", args={"spec": task.target}, outputs=["path"]),
        ]

        # A workflow can provide the final content as a resolved Task input.
        # Planner-created WRITE tasks do not have that input, so generate the
        # file content in a dedicated LLM step instead of passing the path as
        # content (the old behavior caused every write to fail or lie).
        content = task.inputs.get("content")
        task_text = f"{task.goal} {task.description}".lower()
        mode = task.inputs.get("mode") or (
            "append"
            if any(token in task_text for token in ("追加", "附加", "append", "add to", "末尾"))
            else "overwrite"
        )
        if content is None:
            research_context = task.inputs.get("research_context")
            context_suffix = ""
            if research_context:
                context_suffix = (
                    "\n\n已完成的前置检索/分析结果（仅作为事实依据，勿编造来源）：\n"
                    f"{str(research_context)[:6000]}"
                )
            steps.append(
                ExecutionStep(
                    tool="llm",
                    args={
                        "verb": "write",
                        "target": task.target,
                        "prompt": (
                            "你是文件生成器。只输出目标文件的完整内容，"
                            "不要解释，不要 markdown 代码围栏。"
                        ),
                        "user": (
                            f"目标文件: {task.target}\n"
                            f"任务: {task.goal}\n"
                            f"说明: {task.description}"
                            f"{context_suffix}"
                        ),
                    },
                    outputs=["content"],
                )
            )
            content_arg = "$content"
        else:
            content_arg = str(content)

        steps.append(
            ExecutionStep(
                tool="filesystem.write",
                args={"path": "$path", "content": content_arg, "mode": mode},
                outputs=["result"],
            )
        )
        return ExecutionPlan(
            task=task,
            steps=steps,
        )
