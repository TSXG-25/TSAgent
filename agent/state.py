"""Agent State — Plan-and-Act 状态模型。

Planner 输出纯 Goal 分解（没有 Tool）。
Executor 自主决定工具。
Reflector 基于 success_condition 做反思。
"""
from typing import Annotated, Any, Dict, List, Optional
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

# ── Runtime Cache ──
class AgentState(TypedDict, total=False):
    """可变 Runtime Cache，不是新的 Task Source of Truth。

    ``plan`` is the serialized projection of canonical ``agent.task.Task``
    objects. Execution status is intentionally mutable here because this is
    the Runtime Cache; new contracts use ``Task`` / ``ExecutionPlan`` directly.
    """
    messages: Annotated[list[BaseMessage], add_messages]
    # Plan
    plan: Optional[List[Dict[str, Any]]]    # canonical Task 的 Runtime Cache 投影
    execution_plans: List[Any]              # Compiler 产出的 ExecutionPlan 列表
    current_task_index: int                 # 当前执行到第几个任务
    # Artifacts
    artifacts: Dict[str, Any]               # 关键产出
    # Context
    memory_context: Optional[str]
    repo_context: Optional[str]
    skill_hint: str
    retries: int
    workflow: Optional[str]
    conversation_intent: str
    reflection: Optional[Dict]
    decision: Optional[Dict]
    diagnostics: List[Dict[str, Any]]
    resolved_target: str
    resolved_symbol: str
    conversation_snapshot: Any
    conversation_reference_type: str
    conversation_runtime_continuation: str
    conversation_clarification_required: bool
    # Terminal outcome projected by UniversalAgent for the Service boundary.
    # These are facts, not a second Run state machine.
    runtime_terminal_status: str
    runtime_failure_code: str
    budget_exhausted: bool
    # v2.3H2: deterministic world-effect truth projected by Runtime.
    effect_class: str
    required_effects: List[Dict[str, Any]]
    verified_effects: List[Dict[str, Any]]
    unsupported_effects: List[Dict[str, Any]]
    failed_effects: List[Dict[str, Any]]
    unresolved_required_effects: List[Dict[str, Any]]
    effect_truth_ok: bool
