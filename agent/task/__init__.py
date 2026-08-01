"""Task Model — Planner output and Execution Plan.

Task represents a goal. ExecutionPlan represents how to achieve it.
Task does NOT know about tools. ToolSelector (Compiler) maps Task → ExecutionPlan.

One level, one model:
- Planning layer: Task (with policy: retry/budget/validator/tool_policy/executor)
- Compilation layer: ExecutionPlan (steps + executor: "tool" | "llm")
- Execution layer: ExecutionStep (atomic tool invocation)

Stage is a Task template with the same policy fields — no separate Stage model
in the execution chain. WorkflowExecutor projects Stage → Task via stage.to_task().
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class Verb(Enum):
    """Fixed set of verbs that Planner can output.
    
    Each verb maps to a specific capability in ToolSelector (Compiler).
    No free-form strings — ensures deterministic tool selection.
    """
    RESOLVE = "resolve"       # workspace.resolve() — always first step
    READ = "read"             # filesystem.read()
    WRITE = "write"           # filesystem.write()
    MODIFY = "modify"         # resolve → read → llm edit → write
    EXPLAIN = "explain"       # resolve → read → llm summarize
    SEARCH = "search"         # repository.search_similar()
    LIST = "list"             # knowledge or filesystem.list()
    DELETE = "delete"         # filesystem.delete()
    MOVE = "move"             # filesystem.move()
    EXECUTE = "execute"       # shell.execute()


@dataclass
class TaskPolicy:
    """Execution policy attached to a Task.

    Replaces the separate Stage/ExecutionSpec concept in the execution chain.
    A Stage is just a Task template carrying the same policy fields.

    Attributes:
        executor: Which executor should run this task. Decided by the Compiler
            (ToolSelector). "tool" = deterministic plan execution,
            "llm" = open-ended LLM reasoning (no tool invocation).
        max_retries: Retry count for tools.
        timeout: Timeout seconds (None = unlimited).
        max_tokens: Max output tokens (LLM tasks only).
        budget: BudgetSpec.to_dict() — resource budget (no import to avoid cycles).
        validators: Validator objects/callables for success checking.
        tool_policy: {"allow": [tool names]} — restricted tool access.
        required_outputs: Artifact types required before execution.
    """
    executor: str = "tool"                     # "tool" | "llm"
    max_retries: int = 0
    timeout: Optional[int] = None
    max_tokens: Optional[int] = None
    budget: Optional[Dict[str, Any]] = None
    validators: List[Any] = field(default_factory=list)
    tool_policy: Optional[Dict[str, Any]] = None
    required_outputs: List[str] = field(default_factory=list)


@dataclass
class Task:
    """A single unit of work output by the Planner.

    Does NOT know about tools or execution.
    ToolSelector (Compiler) maps verb + target + kind → ExecutionPlan.

    Attributes:
        id: Unique task ID (e.g. "task-1")
        verb: Action verb (fixed enum, not free-form)
        target: What to act on (e.g. "runtime.py", "ProjectIndex", "workflows")
        kind: Type of target ("file", "symbol", "directory", "workflow", "tool", "concept")
        goal: Human-readable description
        dependencies: IDs of tasks that must complete first
        policy: Execution policy (retry/budget/validator/tool_policy/executor)
    """
    id: str
    verb: Verb
    target: str
    kind: str = ""                     # "file", "symbol", "directory", ...
    goal: str = ""
    dependencies: list[str] = field(default_factory=list)
    status: str = "pending"            # runtime state, not part of plan
    policy: TaskPolicy = field(default_factory=TaskPolicy)

    def to_dict(self) -> dict:
        """Serialize to dict for LLM output parsing backward compat."""
        return {
            "id": self.id,
            "verb": self.verb.value,
            "target": self.target,
            "kind": self.kind,
            "goal": self.goal or f"{self.verb.value} {self.target}",
            "dependencies": list(self.dependencies),
            "status": self.status,
        }

    @staticmethod
    def from_dict(d: dict) -> "Task":
        """Deserialize from dict (Planner JSON output)."""
        verb_str = d.get("verb", "RESOLVE")
        try:
            verb = Verb(verb_str.lower())
        except ValueError:
            verb = Verb.RESOLVE
        policy_data = d.get("policy") or {}
        return Task(
            id=d.get("id", "task-1"),
            verb=verb,
            target=d.get("target", ""),
            kind=d.get("kind", ""),
            goal=d.get("goal", ""),
            dependencies=d.get("dependencies", []),
            status=d.get("status", "pending"),
            policy=TaskPolicy(
                executor=policy_data.get("executor", "tool"),
                max_retries=policy_data.get("max_retries", 0),
                timeout=policy_data.get("timeout"),
                max_tokens=policy_data.get("max_tokens"),
                budget=policy_data.get("budget"),
            ),
        )


@dataclass
class ExecutionStep:
    """A single atomic tool invocation within an ExecutionPlan.

    Attributes:
        tool: Tool name (e.g. "workspace", "filesystem.read", "llm", "repository")
        args: Arguments to pass to the tool
        outputs: Output keys produced (for passing between steps)
    """
    tool: str
    args: dict = field(default_factory=dict)
    outputs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"tool": self.tool, "args": dict(self.args), "outputs": list(self.outputs)}


@dataclass
class ExecutionPlan:
    """Deterministic execution plan produced by ToolSelector (Compiler).

    No LLM involved — pure rule-based mapping from Task.

    Attributes:
        task: The Task this plan executes.
        steps: Ordered atomic tool invocations.
        executor: Which executor should run this plan.
            "tool" → PlanExecutor (deterministic step execution).
            "llm" → LLMExecutor (open-ended reasoning, steps empty).
    """
    task: Task
    steps: list[ExecutionStep] = field(default_factory=list)
    executor: str = "tool"

    def to_dict(self) -> dict:
        return {
            "task": self.task.to_dict(),
            "steps": [s.to_dict() for s in self.steps],
            "step_count": len(self.steps),
            "executor": self.executor,
        }

    def __len__(self) -> int:
        return len(self.steps)

    @property
    def is_llm(self) -> bool:
        """True if this plan is open-ended LLM reasoning (no tool steps)."""
        return self.executor == "llm"