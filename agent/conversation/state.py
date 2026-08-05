"""Conversation Runtime — 当前交互状态机（v2.1B-1，ADR-0013）。

ADR-0013-A：Conversation Runtime stores conversation state, not knowledge.

原则（评审冻结）：
- ConversationState 是 frozen 快照；状态迁移 old_state → update() → new_state，
  与 FailureEvent 一致，支持 Replay / Checkpoint / Time Travel。
- Retriever 不生成 Prompt；只返回 ConversationSnapshot（纯数据），
  Planner / Decision / Direct Chat 自行拼 Prompt。
- 轮次类型由已有 Intent Engine 派生（ConversationIntent）；续接语义使用 ADR-0013
  冻结的短语集合，不把模糊的“继续”永久解释成某一种行为。
- update() 记录 ConversationEvent，供 Conversation Replay。
"""
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Deque, Dict, List, Optional, Protocol, runtime_checkable


@dataclass(frozen=True)
class ConversationState:
    """当前交互状态快照（immutable）。唯一写入入口是 ConversationTracker。"""
    user_id: str
    recent_goal: str = ""          # = 最近一次 NEW_REQUEST 的原始 user_input（不做摘要）
    last_instruction: str = ""     # = 上一条 NEW_REQUEST 的原始 user_input
    last_answer: str = ""          # = 上一条 assistant 回答
    turn_count: int = 0
    updated_at: float = 0.0


@dataclass(frozen=True)
class ConversationSnapshot:
    """Retriever 返回的纯数据快照（不携带 Prompt 生成职责）。"""
    recent_goal: str = ""
    last_instruction: str = ""
    last_answer: str = ""


class ReferenceType(Enum):
    """引用问题的目标字段（Conversation Reference Resolver 输出）。

    只注入对应字段，避免把 recent_goal/last_answer 一起塞给 LLM 让它猜。
    """
    LAST_GOAL = "last_goal"              # "刚才让我做什么 / 最近目标"
    LAST_INSTRUCTION = "last_instruction"  # "上一条指令 / 上一条让我做什么"
    LAST_ANSWER = "last_answer"          # "刚才答案是多少 / 上一条回答"
    LAST_RUNTIME = "last_runtime"        # "继续 / 恢复" → 从 Runtime 状态恢复执行
    UNKNOWN = "unknown"


class ConversationIntent(Enum):
    NEW_REQUEST = "new_request"    # 新指令 / 新问题 / 新事实
    REFERENCE = "reference"        # 引用上轮内容（"刚才/上一条/上一题/刚才的答案"）
    CONTINUE_PLAN = "continue_plan"  # 恢复未完成 Runtime 计划
    CONTINUE_CHAT = "continue_chat"  # 延续上一条回答/解释
    CONTINUE_REFERENCE = "continue_reference"  # 延续带省略目标的引用
    # 兼容 v2.1B-1 的事件消费者；新事件不得再使用这个宽泛别名。
    CONTINUATION = "continue_plan"

    @classmethod
    def _missing_(cls, value):
        # Replay/外部缓存中的旧事件可能仍携带 "continuation"。
        if value == "continuation":
            return cls.CONTINUE_PLAN
        return None


@dataclass(frozen=True)
class ConversationEvent:
    """一次轮次的状态迁移记录（Replay 用）。"""
    index: int
    intent: ConversationIntent
    user_input: str
    answer: str
    ts: float


@runtime_checkable
class ConversationRetrieverProtocol(Protocol):
    """Static boundary consumed by Planner / Decision / Direct Chat."""

    def get(self, user_id: str) -> ConversationState:
        ...

    def snapshot(self, user_id: str) -> ConversationSnapshot:
        ...

    def runtime_pending(self, user_id: str) -> bool:
        ...

    def events(self, user_id: str) -> List[ConversationEvent]:
        ...


# ADR-0013：Continuation Contract。
#
# 这些集合是有意保持小而冻结的；新增自然语言表达应先修改契约与 benchmark，
# 不能在 Conversation Runtime 中不断堆叠 regex。
_PLAN_CONTINUATION_TOKENS = frozenset({
    "继续执行", "继续执行任务", "继续执行未完成的任务", "继续执行未完成任务",
    "继续任务", "继续做", "继续处理",
    "继续完成", "完成剩余任务", "恢复任务", "接着做", "接着执行",
    "resume task", "continue task", "continue execution",
})
_CHAT_CONTINUATION_TOKENS = frozenset({
    "继续讲", "继续解释", "继续回答", "接着说", "展开说",
    "再详细一点", "详细一点", "continue", "go on",
})
_BARE_CONTINUATION_TOKENS = frozenset({
    "继续", "继续吧", "接着", "接着来", "然后呢", "还有吗",
})
_REFERENCE_CONTINUATION_TOKENS = frozenset({
    "那个呢", "这个呢", "上一个呢", "前面那个", "继续那个", "继续这个",
})
_REFERENCE_CONTINUATION_MARKERS = frozenset({
    "刚才", "上一个", "前面", "那个", "这个", "第一个", "第二个",
    "第三个", "最后一个",
})

# REFERENCE 动作前缀（Intent Engine 的 action 名；稳定前缀，非增长正则）
_REFERENCE_ACTION_PREFIXES = ("query", "recall", "ask", "reference")

# 目标型任务域：会"改变世界状态"或"交付产物"的 domain。
# 闲聊/查询/数学/天气等填充轮（knowledge/math/chat/translation/creation/memory）
# 不算"目标"，不会覆盖 recent_goal —— 否则 4 轮闲聊后"刚才让我做什么"会答成闲聊内容。
_TASK_DOMAINS = frozenset({
    "development", "file", "office", "operation", "scheduling",
})


def _is_goal_request(intent: Optional[object]) -> bool:
    """NEW_REQUEST 是否值得记为最近目标（目标型）。

    确定性规则：requires_execution=True 且 domain 属于任务域。
    不解析中文（ADR-0013 约束②）。
    """
    if intent is None:
        return False
    return bool(getattr(intent, "requires_execution", False)) and \
        getattr(intent, "domain", None) in _TASK_DOMAINS


def classify_conversation_intent(
    intent: Optional[object],
    user_input: str,
    *,
    runtime_pending: bool = False,
) -> ConversationIntent:
    """把一轮输入归类为新的 continuation contract。

    语言理解由 Intent Engine 提供；Conversation Runtime 只消费结构化 action，
    并使用少量冻结短语集合完成 continuation 的契约分流。

    裸“继续/接着/然后呢”本身不携带行为语义：
    - Runtime 有未完成计划时 → CONTINUE_PLAN；
    - 否则 → CONTINUE_CHAT。
    这样 benchmark 不会把一个天然歧义词硬编码成某种行为。
    """
    text = (user_input or "").strip().lower()
    if text in _PLAN_CONTINUATION_TOKENS:
        return ConversationIntent.CONTINUE_PLAN
    # Explicit user wording is resolved before Runtime state. A pending bit
    # must not swallow "继续那个" into plan recovery.
    if text in _REFERENCE_CONTINUATION_TOKENS or (
        text.startswith(("继续", "接着"))
        and any(marker in text for marker in _REFERENCE_CONTINUATION_MARKERS)
    ):
        return ConversationIntent.CONTINUE_REFERENCE
    if text in _CHAT_CONTINUATION_TOKENS:
        return ConversationIntent.CONTINUE_CHAT
    if text in _BARE_CONTINUATION_TOKENS:
        return (
            ConversationIntent.CONTINUE_PLAN
            if runtime_pending else ConversationIntent.CONTINUE_CHAT
        )
    if intent is not None:
        domain = getattr(intent, "domain", None)
        action = str(getattr(intent, "action", "")).lower()
        reference_kind = str(getattr(intent, "reference_kind", "")).lower()
        if reference_kind == "instruction" and text.startswith(("继续", "接着")):
            return ConversationIntent.CONTINUE_REFERENCE
        if domain == "memory" and action.startswith(_REFERENCE_ACTION_PREFIXES):
            return ConversationIntent.REFERENCE
    return ConversationIntent.NEW_REQUEST


class ConversationTracker:
    """唯一写入入口：state --(event)--> new_state。"""

    def __init__(self, max_events: int = 50) -> None:
        self._max_events = max_events
        self._states: Dict[str, ConversationState] = {}
        self._events: Dict[str, Deque[ConversationEvent]] = {}
        # 仅保存 Runtime 提供的 pending 布尔信号，不保存 active_task/plan。
        self._runtime_pending: Dict[str, bool] = {}

    def update(
        self,
        *,
        user_id: str,
        user_input: str,
        assistant_answer: str = "",
        intent: Optional[object] = None,
        runtime_pending: Optional[bool] = None,
    ) -> ConversationState:
        """每轮 answer 生成后调用。确定性派生，无 LLM。

        NEW_REQUEST  → 更新 recent_goal / last_instruction（= 原始 user_input）。
        REFERENCE / 任一 CONTINUE_* → 不覆盖 recent_goal / last_instruction。
        所有轮次都更新 last_answer，turn_count += 1，并追加 ConversationEvent。

        runtime_pending 是 Runtime 提供的布尔信号，不是 active_task 的第二份
        存储；ConversationState 仍不包含 plan/task。
        """
        # ``runtime_pending`` describes the Runtime state after this turn.  The
        # current input must be classified with the signal left by the prior turn.
        pending_for_input = self._runtime_pending.get(user_id, False)
        kind = classify_conversation_intent(
            intent, user_input, runtime_pending=pending_for_input,
        )
        old = self._states.get(user_id) or ConversationState(user_id=user_id)
        now = time.time()

        if runtime_pending is not None:
            self._runtime_pending[user_id] = bool(runtime_pending)

        if kind is ConversationIntent.NEW_REQUEST and _is_goal_request(intent):
            # 目标型新指令 → 更新最近目标 / 上一条指令
            recent_goal = user_input
            last_instruction = user_input
        elif kind is ConversationIntent.NEW_REQUEST:
            # 闲聊/查询填充轮：不覆盖目标，也不视为"上一条指令"
            recent_goal = old.recent_goal
            last_instruction = old.last_instruction
        else:
            # REFERENCE / CONTINUE_*：保持最近目标与上一条指令
            recent_goal = old.recent_goal
            last_instruction = old.last_instruction

        new = ConversationState(
            user_id=user_id,
            recent_goal=recent_goal,
            last_instruction=last_instruction,
            last_answer=assistant_answer,
            turn_count=old.turn_count + 1,
            updated_at=now,
        )

        self._states[user_id] = new
        self._events.setdefault(user_id, deque(maxlen=self._max_events)).append(
            ConversationEvent(
                index=new.turn_count, intent=kind,
                user_input=user_input, answer=assistant_answer, ts=now,
            )
        )
        return new

    def get_state(self, user_id: str) -> ConversationState:
        return self._states.get(user_id) or ConversationState(user_id=user_id)

    def get_events(self, user_id: str) -> List[ConversationEvent]:
        return list(self._events.get(user_id, []))

    def runtime_pending(self, user_id: str) -> bool:
        """返回 Runtime 最近提供的 pending 信号，不暴露 plan/task。"""
        return self._runtime_pending.get(user_id, False)

    def reset(self, user_id: str) -> None:
        """清理一个会话的快照、事件和 pending 信号。"""
        self._states.pop(user_id, None)
        self._events.pop(user_id, None)
        self._runtime_pending.pop(user_id, None)


class ConversationRetriever:
    """唯一读取入口。返回冻结快照 / 事件日志，不生成 Prompt。"""

    def __init__(self, tracker: Optional[ConversationTracker] = None) -> None:
        self._tracker = tracker or ConversationTracker()

    @property
    def tracker(self) -> ConversationTracker:
        return self._tracker

    def get(self, user_id: str) -> ConversationState:
        return self._tracker.get_state(user_id)

    def snapshot(self, user_id: str) -> ConversationSnapshot:
        s = self.get(user_id)
        return ConversationSnapshot(
            recent_goal=s.recent_goal,
            last_instruction=s.last_instruction,
            last_answer=s.last_answer,
        )

    def runtime_pending(self, user_id: str) -> bool:
        """委托 tracker：Runtime 最近提供的 pending 信号（只读）。"""
        return self._tracker.runtime_pending(user_id)

    def events(self, user_id: str) -> List[ConversationEvent]:
        return self._tracker.get_events(user_id)


def render_snapshot(snapshot: ConversationSnapshot) -> str:
    """供 Planner / Decision / Direct Chat 拼 Prompt 的纯文本帮助函数。

    Retriever 本身不生成 Prompt；这是消费者主动调用的渲染工具。

    含字段-问题映射规则：Memory Fuzz 发现 LLM 回答"刚才让我做什么"时
    会误用【上一条回答】（如"2+2=4"）而非【最近目标】，需显式消歧。
    """
    parts = ['## 会话状态（回答"刚才/上一条/继续"时必须优先使用）']
    if snapshot.recent_goal:
        parts.append(f"- 最近目标: {snapshot.recent_goal}")
    if snapshot.last_instruction:
        parts.append(f"- 上一条指令: {snapshot.last_instruction}")
    if snapshot.last_answer:
        parts.append(f"- 上一条回答: {snapshot.last_answer}")
    if len(parts) == 1:
        return ""
    parts.append(
        "使用规则：\n"
        "- 问'刚才让我做什么 / 上一条指令 / 最近目标' → 用【最近目标】或【上一条指令】\n"
        "- 问'刚才答案是多少 / 上一条回答' → 用【上一条回答】\n"
        "- 禁止用【上一条回答】回答'刚才让我做什么'"
    )
    return "\n".join(parts)


# ── Conversation Reference Resolver（v2.1B-2）──
# 输入是 Intent Engine 的结构化输出（intent.reference_kind）；本层不解析中文。
_REFERENCE_KIND_MAP = {
    "goal": ReferenceType.LAST_GOAL,
    "instruction": ReferenceType.LAST_INSTRUCTION,
    "answer": ReferenceType.LAST_ANSWER,
    "runtime": ReferenceType.LAST_RUNTIME,
}


def resolve_reference_type(intent: Optional[object]) -> ReferenceType:
    """把 REFERENCE / CONTINUE_* 意图映射到要检索的字段。

    纯映射，零 regex；语言理解（reference_kind）由 Intent Engine 负责。
    """
    if intent is None:
        return ReferenceType.UNKNOWN
    kind = str(getattr(intent, "reference_kind", "") or "").strip().lower()
    return _REFERENCE_KIND_MAP.get(kind, ReferenceType.UNKNOWN)


def render_reference(snapshot: ConversationSnapshot,
                     ref_type: ReferenceType) -> str:
    """按 ReferenceType 只渲染对应字段（字段级注入；消费者主动调用）。"""
    if ref_type is ReferenceType.LAST_GOAL:
        return f"## 会话状态\n- 最近目标（回答'刚才让我做什么/最近目标'时必须使用）: {snapshot.recent_goal}"
    if ref_type is ReferenceType.LAST_INSTRUCTION:
        return f"## 会话状态\n- 上一条指令（回答'上一条让我做什么'时必须使用）: {snapshot.last_instruction}"
    if ref_type is ReferenceType.LAST_ANSWER:
        return f"## 会话状态\n- 上一条回答（回答'刚才答案是多少/上一条回答'时必须使用）: {snapshot.last_answer}"
    return ""


# 全局单例（Runtime 共享；每轮 update 后状态立即可读）
conversation_tracker = ConversationTracker()
conversation_retriever: ConversationRetrieverProtocol = ConversationRetriever(
    conversation_tracker
)
