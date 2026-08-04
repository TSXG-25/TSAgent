"""ListRule — verb=LIST → knowledge (for workflows/tools) or filesystem (for directories).

Uses knowledge for listing project capabilities.
Uses workspace for listing directory contents.
"""
from agent.task import Task, Verb, ExecutionPlan, ExecutionStep
from agent.compiler.tool_selector import Rule


# Keywords that map to knowledge queries (not filesystem)
KNOWLEDGE_TARGETS = {
    "workflow", "workflows", "tool", "tools", "skill", "skills",
    "capability", "capabilities", "service", "services",
    "registry", "registries", "prompt", "prompts",
}


class ListRule(Rule):
    """LIST "workflows" → knowledge.workflows. LIST "src" → filesystem.list(src)."""

    @property
    def verb(self) -> Verb:
        return Verb.LIST

    def matches(self, task: Task) -> bool:
        return task.verb == Verb.LIST

    def build(self, task: Task, **services) -> ExecutionPlan:
        target_lower = task.target.lower().strip()

        # Knowledge targets: list from registry
        if target_lower in KNOWLEDGE_TARGETS:
            return ExecutionPlan(
                task=task,
                steps=[
                    ExecutionStep(
                        tool="knowledge",
                        args={"query": task.target},
                        outputs=["items"],
                    ),
                ],
            )

        # Workspace directory listing (for "list src/", "list files", etc.)
        return ExecutionPlan(
            task=task,
            steps=[
                ExecutionStep(
                    tool="workspace",
                    args={"spec": task.target},
                    outputs=["path"],
                ),
                ExecutionStep(
                    tool="filesystem.list",
                    args={"path": "$path"},
                    outputs=["items"],
                ),
            ],
        )
