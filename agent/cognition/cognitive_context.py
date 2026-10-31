"""CognitiveContext — Agent 认知层统一上下文。

CognitiveContext 是纯数据容器，零方法、零 import 外部服务。
"""
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional


if TYPE_CHECKING:
    from agent.workspace import WorkspaceContext


@dataclass
class CognitiveContext:
    """认知层统一上下文。

    这是整个 Agent 认知阶段的唯一输入数据结构。
    所有认知模块（ReferenceResolver、IntentEngine、WorkflowRouter、Planner）
    都只接收这个数据结构。

    由 Orchestrator 在 plan() 开始时构建。
    """
    # 用户当前输入
    query: str

    # 对话历史 — 最近 N 轮 [(role, content), ...]
    conversation: list[dict] = field(default_factory=list)

    # Durable, session-scoped results; assistant prose is not execution evidence.
    run_results: list[dict] = field(default_factory=list)

    # Workspace 上下文（来自 WorkspaceService）
    workspace: Optional["WorkspaceContext"] = None  # noqa: F821

    # 当前执行计划（多轮对话时存在）
    plan: list[dict] = field(default_factory=list)

    # 当前正在执行的任务
    task: Optional[dict] = None

    # Runtime 对未完成执行目标的只读投影；ConversationState 不保存 plan/task。
    runtime_pending_target: str = ""

    # Repository 上下文（代码片段/符号索引）
    repository_context: str = ""

    # 用户长期记忆（偏好、事实）
    memory: dict = field(default_factory=dict)

    # 当前 Artifacts
    artifacts: dict = field(default_factory=dict)

    # ── 便捷属性（简化下游访问） ──

    @property
    def current_file(self) -> Optional[str]:
        """Workspace 当前打开的文件。"""
        if self.workspace:
            return self.workspace.current_file
        return None

    @property
    def current_symbol(self) -> Optional[str]:
        """Workspace 当前关注的符号。"""
        if self.workspace:
            return self.workspace.current_symbol
        return None

    def short_summary(self) -> str:
        """简短上下文摘要（用于 LLM Prompt 注入）。"""
        parts = []
        if self.current_file:
            parts.append(f"当前文件: {self.current_file}")
        if self.current_symbol:
            parts.append(f"当前符号: {self.current_symbol}")
        if self.conversation:
            parts.append(f"对话轮次: 最近{len(self.conversation)}轮")
        return " | ".join(parts) if parts else "无上下文"


@dataclass
class PlannerContext(CognitiveContext):
    """Canonical PLAN phase view.

    This compatibility subtype keeps the frozen CognitiveContext contract
    valid for existing Resolver/Intent callers while making the Runtime's
    phase boundary explicit.

    The continuation fields below are a narrow Runtime projection.  They are
    intentionally separate from ``plan``/``task`` so a Planner can learn what
    has already been established without receiving a raw checkpoint or the
    mutable Runtime state dump.
    """

    # Runtime-projected task facts used by continuation/resume planning.  The
    # dictionaries contain only id/verb/target/target_type/status/dependencies.
    completed_tasks: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    established_facts: tuple[str, ...] = field(default_factory=tuple)
    available_artifacts: tuple[str, ...] = field(default_factory=tuple)
    continuation_scope: tuple[dict[str, Any], ...] = field(default_factory=tuple)
