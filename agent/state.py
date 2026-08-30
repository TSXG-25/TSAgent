"""Agent State — Plan-and-Act 状态模型。

Planner 输出纯 Goal 分解（没有 Tool）。
Executor 自主决定工具。
Reflector 基于 success_condition 做反思。
"""
from __future__ import annotations

from typing import Annotated, Any, Dict, List, Optional
from langchain_core.messages import BaseMessage
from typing_extensions import TypedDict


def _add_messages(left, right):
    """Load the LangGraph reducer only when a graph actually uses the state.

    Importing ``langgraph.graph.message`` initializes the whole LangGraph SDK
    and its optional provider dependencies.  AgentState is also imported by
    lightweight compiler and execution tests, so that import must not happen
    during package/test collection.
    """
    from langgraph.graph.message import add_messages

    return add_messages(left, right)

# ── Runtime Cache ──
class AgentState(TypedDict, total=False):
    """可变 Runtime Cache，不是新的 Task Source of Truth。

    ``plan`` is the serialized projection of canonical ``agent.task.Task``
    objects. Execution status is intentionally mutable here because this is
    the Runtime Cache; new contracts use ``Task`` / ``ExecutionPlan`` directly.
    """
    messages: Annotated[list[BaseMessage], _add_messages]
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
    context_policy: str
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
    runtime_failure_class: str
    runtime_failure_retryable: bool
    runtime_failure_kind: str
    runtime_failure_source: str
    runtime_failure: Dict[str, Any]
    runtime_failure_message: str
    recovery_directive: Dict[str, Any]
    structural_recovery_count: int
    action_retry_count: int
    budget_exhausted: bool
    replan_skipped_verified_effects: int
    # v2.3H2: deterministic world-effect truth projected by Runtime.
    effect_class: str
    required_effects: List[Dict[str, Any]]
    verified_effects: List[Dict[str, Any]]
    unsupported_effects: List[Dict[str, Any]]
    failed_effects: List[Dict[str, Any]]
    unresolved_required_effects: List[Dict[str, Any]]
    effect_truth_ok: bool
    # v2.3H3: deterministic freshness/output requirements projected from the
    # Intent and consumed by the Runtime completion gate.
    freshness_required: bool
    source_grounding_required: bool
    fresh_evidence: bool
    answer_required: bool
    user_visible_output_verified: bool
    request_output: bool
    # v2.3H4a/b: facts derived from the original request, never from Planner prose.
    requested_outcomes: List[str]
    authorized_write_scopes: List[str]
    execution_evidence: List[Dict[str, Any]]
    unresolved_requested_outcomes: List[str]
    # Goal/Action projections. These are ephemeral Runtime inputs/evidence;
    # durable Run facts remain in the Store/Event/Checkpoint contracts.
    goal_state: Dict[str, Any]
    goal_evidence: List[Dict[str, Any]]
    goal_missing: List[str]
    inbox: Dict[str, Any]
    next_action: Dict[str, Any]
    last_action_result: Dict[str, Any]
    answer_ready: bool
    execution_mode: str
    execution_ownership: Dict[str, Dict[str, Any]]
    resolved_execution_ownership: Dict[str, Dict[str, Any]]
