"""Agent State — Plan-and-Act 状态模型。

Planner 输出纯 Goal 分解（没有 Tool）。
Executor 自主决定工具。
Reflector 基于 success_condition 做反思。
"""
from typing import Annotated, Any, Dict, List, Optional
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

# ── 向后兼容的旧 Task 类型（旧 workflow/planner 可能还在用） ──
class LegacyTask(TypedDict):
    goal: str
    status: str
    tool: Optional[str]
    parameters: Dict[str, Any]
    depends_on: List[int]
    observation: str


# ── 新 AgentState ──
class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    # Plan
    plan: Optional[List[Dict[str, Any]]]    # Planner 输出（新 Task 模型的 dict 表示）
    current_task_index: int                 # 当前执行到第几个任务
    # Artifacts
    artifacts: Dict[str, Any]               # 关键产出
    # Context
    memory_context: Optional[str]
    repo_context: Optional[str]
    skill_hint: str
    retries: int
    workflow: Optional[str]
    reflection: Optional[Dict]