"""Executor — 执行器包。

架构：
- contract: 统一执行器契约（execute(target, context) -> ExecutionResult）+ ExecutorFactory
- executors/: 并列执行器
    - tool.py:     ToolExecutor（确定性 ExecutionPlan 步骤序列）
    - llm.py:      LLMExecutor（纯 LLM 推理）— 暂留在 llm_executor.py，Phase C 迁移
    - workflow.py: WorkflowExecutor（消费整个 Workflow DAG）

选择执行器只通过 ExecutorFactory（按 ExecutionPlan.executor_type），
使用方不做 if/elif 选择。旧 Stage 体系（ExecutorRegistry）已在 Phase B.4 移除。
"""
from .contract import (
    Executor as ExecutorContract,
    ExecutorFactory,
    executor_factory,
    ExecutionTarget,
)
from .executors.tool import ToolExecutor as PlanToolExecutor
from .executors.workflow import WorkflowExecutor as V2WorkflowExecutor


class _LazyLLMExecutor:
    """Keep Provider imports off the application bootstrap path."""

    def __new__(cls, *args, **kwargs):
        from .llm_executor import LLMExecutor

        return LLMExecutor(*args, **kwargs)

# 注册统一契约执行器
executor_factory.register("tool", PlanToolExecutor)
executor_factory.register("llm", _LazyLLMExecutor)
executor_factory.register("workflow", V2WorkflowExecutor)

__all__ = [
    "LLMExecutor",
    "PlanToolExecutor",
    "V2WorkflowExecutor",
    "ExecutorContract",
    "ExecutorFactory",
    "executor_factory",
    "ExecutionTarget",
]


def __getattr__(name: str):
    if name == "LLMExecutor":
        from .llm_executor import LLMExecutor

        return LLMExecutor
    raise AttributeError(name)
