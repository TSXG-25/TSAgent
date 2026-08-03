"""CognitiveContext — Agent 认知层统一上下文。

CognitiveContext 是纯数据容器，零方法、零 import 外部服务。
整个认知链路上的所有模块（ReferenceResolver、IntentEngine、WorkflowRouter、Planner）
都依赖这个统一的数据结构。

设计原则：
1. 纯数据类 — 没有方法，没有 import
2. 由 Orchestrator 统一构建
3. 下游模块只读，不修改
"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ResolutionCandidate:
    """统一解析候选（ADR-0008 Resolution Contract）。

    Reference / Repository / Memory 等所有 Resolver 共用同一模型。
    kind 标识候选类型；confidence 由 merge 用于择优；source 记录来源。
    """
    kind: str = "unknown"        # "topic" | "symbol" | "file" | "ordinal" | "reference" | "unknown"
    target: Optional[str] = None
    confidence: float = 0.0
    reason: str = ""
    source: str = ""

    def to_resolved_query(self, raw: str) -> "ResolvedQuery":
        return ResolvedQuery(
            target=self.target or "",
            symbol=self.target if self.kind == "symbol" else "",
            raw=raw,
            confidence=self.confidence,
            resolution_trace=self.reason,
        )


@dataclass
class ConversationState:
    """跨轮对话状态追踪。

    由 Orchestrator 在每次处理后更新。
    ReferenceResolver 消费这些信息来消歧代词和省略句。
    """
    last_file: Optional[str] = None
    last_symbol: Optional[str] = None
    last_target: Optional[str] = None
    last_action: Optional[str] = None
    last_domain: Optional[str] = None
    last_task: Optional[str] = None
    last_workflow: Optional[str] = None


@dataclass
class ResolvedQuery:
    """ReferenceResolver 消歧后的查询。

    Attributes:
        target: 消歧后的目标对象（文件路径、符号名）
        symbol: 消歧后的符号名（单独的符号字段）
        entities: 从输入中提取的实体列表
        raw: 原始用户输入
        confidence: 消歧置信度
        resolution_trace: 消歧依据（调试用）
    """
    target: str = ""
    symbol: str = ""
    entities: list[str] = field(default_factory=list)
    raw: str = ""
    confidence: float = 0.0
    resolution_trace: str = ""

    @property
    def has_target(self) -> bool:
        return bool(self.target)

    @property
    def has_symbol(self) -> bool:
        return bool(self.symbol)


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

    # 跨轮对话状态
    conversation_state: ConversationState = field(default_factory=ConversationState)

    # Workspace 上下文（来自 WorkspaceService）
    workspace: Optional["WorkspaceContext"] = None  # noqa: F821

    # 当前执行计划（多轮对话时存在）
    plan: list[dict] = field(default_factory=list)

    # 当前正在执行的任务
    task: Optional[dict] = None

    # Repository 上下文（代码片段/符号索引）
    repository_context: str = ""

    # 用户长期记忆（偏好、事实）
    memory: dict = field(default_factory=dict)

    # 当前 Artifacts
    artifacts: dict = field(default_factory=dict)

    # 消歧后的查询（由 ReferenceResolver 填充）
    resolved_query: ResolvedQuery = field(default_factory=ResolvedQuery)

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

    @property
    def last_file(self) -> Optional[str]:
        """上一轮讨论的文件。"""
        return self.conversation_state.last_file if self.conversation_state else None

    @property
    def last_symbol(self) -> Optional[str]:
        """上一轮讨论的符号。"""
        return self.conversation_state.last_symbol if self.conversation_state else None

    @property
    def last_target(self) -> Optional[str]:
        """上一轮的目标。"""
        return self.conversation_state.last_target if self.conversation_state else None

    def short_summary(self) -> str:
        """简短上下文摘要（用于 LLM Prompt 注入）。"""
        parts = []
        if self.current_file:
            parts.append(f"当前文件: {self.current_file}")
        if self.current_symbol:
            parts.append(f"当前符号: {self.current_symbol}")
        if self.last_file:
            parts.append(f"上一轮文件: {self.last_file}")
        if self.last_symbol:
            parts.append(f"上一轮符号: {self.last_symbol}")
        if self.last_target:
            parts.append(f"上一轮目标: {self.last_target}")
        if self.conversation:
            parts.append(f"对话轮次: 最近{len(self.conversation)}轮")
        return " | ".join(parts) if parts else "无上下文"