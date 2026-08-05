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
from collections import deque


@dataclass
class ResolutionCandidate:
    """统一解析候选（ADR-0008 Resolution Contract）。

    Reference / Repository / Memory 等所有 Resolver 共用同一模型。
    kind 标识候选类型；confidence 由 merge 用于择优；source 记录来源。
    symbol: 附加符号（代词消歧等场景同时产出 file+symbol；kind="symbol" 时冗余）。
    """
    kind: str = "unknown"        # "topic" | "symbol" | "file" | "ordinal" | "reference" | "unknown"
    target: Optional[str] = None
    symbol: str = ""
    confidence: float = 0.0
    reason: str = ""
    source: str = ""

    def to_resolved_query(self, raw: str) -> "ResolvedQuery":
        return ResolvedQuery(
            target=self.target or "",
            symbol=self.symbol or (self.target if self.kind == "symbol" else ""),
            raw=raw,
            confidence=self.confidence,
            resolution_trace=self.reason,
            kind=self.kind,
        )


@dataclass
class ResolutionResult:
    """Resolver Pipeline 输出（ADR-0008 / v1.2B 极简）。

    只回答"引用最终解析成了什么"（kind/target/symbol/confidence）。
    domain/action 属于 Intent，不进入 Resolution —— Resolver 决定"指的是谁"，
    Intent 决定"要做什么"，两者组合后再交给 Planner。

    resolved_query: 可选查询视图（兼容下游 intent_engine 对 target/symbol/entities 的读取）。
    to_json(): Determinism Hash 输入（B3：Result Hash + Trace Hash）。
    """
    kind: str = "unknown"        # "topic" | "symbol" | "file" | "ordinal" | "reference" | "unknown"
    target: str = ""             # 解析后的目标（无目标 = ""）
    symbol: str = ""
    confidence: float = 0.0
    trace: str = ""              # 推理路径（Trace Hash 输入）
    raw: str = ""                # 原始输入
    resolved_query: Optional["ResolvedQuery"] = None

    @property
    def resolution_trace(self) -> str:
        return self.trace

    @property
    def has_target(self) -> bool:
        return bool(self.target)

    @property
    def entities(self) -> list:
        """只读委托（兼容下游直接消费 ResolutionResult 的用法）。"""
        if self.resolved_query is not None:
            return self.resolved_query.entities
        return []

    def to_resolved_query(self) -> "ResolvedQuery":
        if self.resolved_query is not None:
            return self.resolved_query
        return ResolvedQuery(
            target=self.target or "",
            symbol=self.symbol or "",
            raw=self.raw,
            confidence=self.confidence,
            resolution_trace=self.trace,
            kind=self.kind,
        )

    def to_json(self) -> dict:
        """Determinism Hash 输入（结果 + 推理路径）。"""
        return {
            "kind": self.kind,
            "target": self.target,
            "symbol": self.symbol,
            "confidence": round(self.confidence, 6),
            "trace": self.trace,
        }


class ResolutionTimeline:
    """能力缓存（v1.2B：State = Cache，Timeline = Storage）。

    - 内部固定窗口 deque（默认 15 轮），对外只见存储接口。
    - 不承载任何 kind 语义 —— "什么叫 symbol/topic/ordinal" 由 Resolver 决定。
    - 语义查询（latest_symbol / nth(kind, n)）由 Resolver 基于 history()/iter_reverse() 实现。
    - 所有 Resolver（Reference / Repository / Memory）共享同一缓存。
    """

    def __init__(self, maxlen: int = 15):
        self._items: deque = deque(maxlen=maxlen)

    def push(self, result: ResolutionResult) -> None:
        """写入（Runtime 唯一调用）。"""
        self._items.append(result)

    def latest(self) -> Optional[ResolutionResult]:
        """最近一条解析结果（无则 None）。"""
        return self._items[-1] if self._items else None

    def history(self) -> list:
        """窗口内全部（旧 → 新）。"""
        return list(self._items)

    def iter_reverse(self):
        """从新到旧迭代（最新优先，用于"最近的"查询）。"""
        return reversed(list(self._items))

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self):
        return iter(self._items)

    def clear(self) -> None:
        self._items.clear()


@dataclass
class ConversationState:
    """跨轮对话状态追踪（v1.2B：State = Cache）。

    唯一字段 timeline：ResolutionResult 窗口缓存（Capability Cache）。
    Resolver 负责推理（从 timeline 派生语义），State 只负责缓存。
    Runtime 通过 record() 写入。
    """
    timeline: ResolutionTimeline = field(default_factory=ResolutionTimeline)

    def record(self, result: ResolutionResult) -> None:
        """唯一写入入口：Runtime 在 Resolver 产出 ResolutionResult 后调用。"""
        self.timeline.push(result)


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
    kind: str = ""              # ResolutionKind（v1.2B：由 Resolver 标注）

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

    # Runtime 对未完成执行目标的只读投影；ConversationState 不保存 plan/task。
    runtime_pending_target: str = ""

    # Repository 上下文（代码片段/符号索引）
    repository_context: str = ""

    # Repository 符号列表（file → [symbols]，有序，Ordinal 解析用；v1.2B B5）
    repository_symbols: dict = field(default_factory=dict)

    # 用户长期记忆（偏好、事实）
    memory: dict = field(default_factory=dict)

    # 跨会话解析事实（Memory Facts，v1.2C；Resolver 保持纯函数）
    memory_resolutions: list = field(default_factory=list)

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
        """上一轮讨论的文件（v1.2B：timeline 派生）。"""
        if self.conversation_state and self.conversation_state.timeline:
            for r in self.conversation_state.timeline.iter_reverse():
                if r.kind == "file" and r.target:
                    return r.target
                if r.target and (r.target.endswith(".py") or r.target.endswith(".js") or "/" in r.target):
                    return r.target
        return None

    @property
    def last_symbol(self) -> Optional[str]:
        """上一轮讨论的符号（v1.2B：timeline 派生）。"""
        if self.conversation_state and self.conversation_state.timeline:
            for r in self.conversation_state.timeline.iter_reverse():
                if r.symbol:
                    return r.symbol
        return None

    @property
    def last_target(self) -> Optional[str]:
        """上一轮的目标（v1.2B：timeline 派生）。"""
        if self.conversation_state and self.conversation_state.timeline:
            latest = self.conversation_state.timeline.latest()
            if latest and latest.target:
                return latest.target
            for r in self.conversation_state.timeline.iter_reverse():
                if r.target:
                    return r.target
        return None

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


@dataclass
class PlannerContext(CognitiveContext):
    """Canonical PLAN phase view.

    This compatibility subtype keeps the frozen CognitiveContext contract
    valid for existing Resolver/Intent callers while making the Runtime's
    phase boundary explicit.
    """
