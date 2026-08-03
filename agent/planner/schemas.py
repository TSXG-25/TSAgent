"""Planner 数据模型 — 纯 Goal 分解。

Planner 不知道工具、不知道能力、不知道执行细节。
只输出 Goal + Success Condition + Dependencies。
"""
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


class Observation(BaseModel):
    """Executor 执行过程中的一次步骤记录。
    
    Planner 不知道这些 — 这是 Executor 的产物。
    summary 字段给 LLM 下一轮思考使用（不需要加载 artifact）。
    """
    action: str = Field(default="", description="执行的动作（capability 标签）")
    status: str = Field(default="succeeded", description="succeeded / failed")
    summary: str = Field(default="", description="人类可读的执行摘要（给 LLM 下一轮看）")
    artifact_ids: List[str] = Field(default_factory=list, description="关联的 Artifact ID 列表")
    tool_used: str = Field(default="", description="实际使用的工具名")
    time_s: float = Field(default=0.0, description="耗时（秒）")


class Task(BaseModel):
    """Planner 输出的单个任务（必须通过核心 Task 契约，ADR-0001）。

    只输出 verb + target + target_type + goal。
    不知道工具、不知道能力、不知道优先级。

    verb 必须是合法枚举值；target_type ∈ file/symbol/text/none。
    file/symbol 类型的 target 必须是具体路径/符号名，禁止中文描述。
    """
    id: str = Field(description="任务唯一标识，如 'task-1', 'task-2'")
    verb: Literal["read", "write", "modify", "execute", "search", "list", "explain", "delete", "move", "resolve"] = Field(default="read", description="动作动词（必须为枚举值）")
    target: str = Field(default="", description="操作对象: 文件路径、符号名、自由文本")
    target_type: Literal["file", "symbol", "text", "none"] = Field(default="none", description="目标类型: file(路径) / symbol(标识符) / text(自由文本) / none")
    goal: str = Field(description="任务目标的简短描述（一句话）")
    description: str = Field(default="", description="任务的详细说明，给 Executor 上下文")
    success_condition: str = Field(description="判断任务成功的条件")

    # DAG 依赖
    dependencies: List[str] = Field(
        default_factory=list,
        description="依赖的 task ID 列表",
    )

    # ── 层级子任务（Tree Planner） ──
    children: List["Task"] = Field(
        default_factory=list,
        description="子任务列表。Executor DFS 递归执行。",
    )

    # ── Executor 填充的字段 ──
    status: str = Field(default="pending", description="pending/running/succeeded/failed/skipped")
    observations: List[Observation] = Field(
        default_factory=list,
        description="Executor 执行记录",
    )
    error: str = Field(default="", description="失败时的错误信息")


class PlanMetadata(BaseModel):
    """Plan 元数据"""
    reasoning: str = Field(description="Planner 为什么这样分解")
    estimated_steps: int = Field(default=0, description="预估步骤数")
    constraints: List[str] = Field(
        default_factory=list,
        description="Planner 识别并遵守的显式约束（v2.0-A Constraint Detection）",
    )


class TaskList(BaseModel):
    """Planner 的最终输出。
    
    纯 Goal 分解。没有任何执行相关信息。
    """
    tasks: List[Task] = Field(description="分解后的任务列表")
    metadata: PlanMetadata = Field(description="Plan 元数据")