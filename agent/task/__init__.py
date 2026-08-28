"""Task Model — Planner output and Execution Plan.

Task represents a goal. ExecutionPlan represents how to achieve it.
Task does NOT know about tools. Compiler maps Task → ExecutionPlan.

One level, one model (ADR-0001):
- Planning layer: Task (Pydantic, the ONLY task model in the system)
- Compilation layer: ExecutionPlan (steps + executor)
- Execution layer: ExecutionStep (atomic tool invocation)

Stage is a Task template with the same policy fields — no separate Stage model
in the execution chain. WorkflowExecutor projects Stage → Task via stage.to_task().

Task is immutable-by-convention (Principle 6): use model_copy(update=...) to
produce modified variants instead of mutating in place.
"""
from dataclasses import dataclass, field
from enum import Enum
import re
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator


class Verb(Enum):
    """Fixed set of verbs that Planner can output.

    Each verb maps to a specific capability in Compiler.
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
    COPY = "copy"             # filesystem.copy()
    EXECUTE = "execute"       # shell.execute()


# target_type 契约值（ADR-0002: Semantic Check 依据）
TARGET_TYPES = ("file", "symbol", "text", "none")


class Observation(BaseModel):
    """一次执行观察，作为 Task 的运行时事实记录。"""
    action: str = ""
    status: str = "succeeded"
    summary: str = ""
    artifact_ids: List[str] = Field(default_factory=list)
    tool_used: str = ""
    time_s: float = 0.0


class TaskPolicy(BaseModel):
    """Execution policy attached to a Task.

    Replaces the separate Stage/ExecutionSpec concept in the execution chain.

    Attributes:
        executor: Which executor should run this task. Decided by the Compiler.
            "tool" = deterministic plan execution, "llm" = open-ended reasoning.
        max_retries: Retry count for tools.
        timeout: Timeout seconds (None = unlimited).
        max_tokens: Max output tokens (LLM tasks only).
        budget: BudgetSpec.to_dict() — resource budget.
        validators: Validator objects/callables for success checking.
        tool_policy: {"allow": [tool names]} — restricted tool access.
        required_outputs: Artifact types required before execution.
        effect_scope: USER_EFFECT or INTERNAL_EXECUTION_EFFECT.
    """
    executor: str = "tool"                     # "tool" | "llm"
    max_retries: int = 0
    timeout: Optional[int] = None
    max_tokens: Optional[int] = None
    budget: Optional[Dict[str, Any]] = None
    validators: List[Any] = Field(default_factory=list)
    tool_policy: Optional[Dict[str, Any]] = None
    required_outputs: List[str] = Field(default_factory=list)
    effect_scope: str = "USER_EFFECT"


class Task(BaseModel):
    """A single unit of work output by the Planner / Workflow.

    This is the ONLY task model in the system (ADR-0001, single-model principle).
    Does NOT know about tools or execution.
    Compiler maps verb + target + target_type → ExecutionPlan.

    Attributes:
        id: Unique task ID (e.g. "task-1")
        verb: Action verb (fixed enum, not free-form)
        target: What to act on (file path / symbol name / free text)
        target_type: Contract type: "file" | "symbol" | "text" | "none"
        goal: Human-readable description
        description: Detailed task description for the executor
        success_condition: Deterministic success condition
        dependencies: IDs of tasks that must complete first
        children: Nested task templates, kept in the same canonical model
        inputs: Resolved argument bindings supplied by workflow stages
        observations: Runtime observations produced by the executor
        status: runtime state (pending/running/succeeded/failed/skipped)
        error: Last execution error, if any
        policy: Execution policy (retry/budget/validator/tool_policy/executor)
    """
    id: str
    verb: Verb = Verb.READ
    target: str = ""
    target_type: Literal["file", "symbol", "text", "none"] = "none"
    goal: str = ""
    description: str = ""
    success_condition: str = ""
    dependencies: list[str] = Field(default_factory=list)
    children: List["Task"] = Field(default_factory=list)
    inputs: Dict[str, Any] = Field(default_factory=dict)
    status: str = "pending"            # runtime state, not part of plan
    observations: List[Observation] = Field(default_factory=list)
    error: str = ""
    policy: TaskPolicy = Field(default_factory=TaskPolicy)

    @model_validator(mode="after")
    def _check_target_contract(self) -> "Task":
        """Semantic check: file/symbol targets must be non-empty (ADR-0002)."""
        if self.target_type in ("file", "symbol") and not self.target.strip():
            raise ValueError(
                f"target_type={self.target_type} 但 target 为空。"
                f"file/symbol 类型必须提供具体路径或符号名，禁止中文描述。"
            )
        return self

    def to_dict(self) -> dict:
        """Serialize the canonical Task for planner/runtime boundaries."""
        d = self.model_dump()
        d["verb"] = self.verb.value
        d["children"] = [child.to_dict() for child in self.children]
        d["observations"] = [observation.model_dump() for observation in self.observations]
        return d

    @staticmethod
    def from_dict(d: dict) -> "Task":
        """Deserialize a canonical Task payload."""
        data = dict(d)
        data.setdefault("target", "")
        data.setdefault("status", "pending")
        # target_type 推断只处理 canonical payloads that omit an optional field.
        if "target_type" not in data:
            data["target_type"] = Task._infer_target_type(data.get("target", ""))
        verb_str = data.get("verb", "read")
        # 严格契约：非法 verb 抛 ValidationError（PR-4，Planner 必须输出合法 verb）
        data["verb"] = Verb(verb_str.lower())
        policy_data = d.get("policy") or {}
        data["policy"] = TaskPolicy(**policy_data) if isinstance(policy_data, dict) else TaskPolicy()
        return Task(**data)

    @staticmethod
    def _infer_target_type(target: str) -> str:
        """从 target 文本推断 target_type（契约适配层，非猜测）。

        - 空 → "none"
        - 含路径特征（/ 或 .ext）→ "file"
        - 其他（标识符/符号名）→ "symbol"
        """
        target = (target or "").strip()
        if not target:
            return "none"
        if "/" in target or "\\" in target or re.search(r"\.\w+", target):
            return "file"
        return "symbol"


# Resolve the recursive children field once, so every boundary uses the same
# canonical Task model instead of planner-specific recursive schemas.
Task.model_rebuild()


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
    """Deterministic execution plan produced by Compiler.

    This is the ONLY IR (intermediate representation) Executor may consume
    (ADR-0001, Principle 8). Immutable-by-convention.

    Attributes:
        task: The Task this plan executes.
        steps: Ordered atomic tool invocations.
        executor: Which executor should run this plan.
            "tool" → ToolExecutor (deterministic step execution).
            "llm" → LLMExecutor (open-ended reasoning, steps empty).
        metadata: Plan metadata (trace, source, etc.).
    """
    task: Task
    steps: list[ExecutionStep] = field(default_factory=list)
    executor: str = "tool"
    metadata: Optional[Dict] = None

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
