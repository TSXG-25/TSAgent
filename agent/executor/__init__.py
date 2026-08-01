"""Executor — 执行器包。

架构：
- contract: 统一执行器契约（execute(target, context) -> ExecutionResult）
- ExecutorFactory: 按 executor_type 解析执行器（使用方不做 if/elif 选择）
- BaseExecutor: 旧 Workflow Stage 执行器基类（Phase B.4 移除）
- ActionResolver: Executor 与 ToolRegistry 之间的隔离层

现有 ReAct Executor（executor.py）保留，
Phase B 迁移为 executors/react.py（ReactExecutor）。
"""
from .contract import (
    Executor as ExecutorContract,
    ExecutorFactory,
    executor_factory,
    ExecutionTarget,
)
from .executor_registry import ExecutorRegistry, BaseExecutor
from .llm_executor import LLMExecutor
from .tool_executor import ToolExecutor
from .workflow_executor import WorkflowExecutor
from .react_executor import ReactExecutor
from .action_resolver import ActionResolver, resolver as action_resolver
from .executors.tool import ToolExecutor as PlanToolExecutor

# 注册全部内置执行器（旧 Stage 体系）
ExecutorRegistry.register("llm", LLMExecutor)
ExecutorRegistry.register("tool", ToolExecutor)
ExecutorRegistry.register("react", ReactExecutor)

# 注册统一契约执行器（Phase B.2+）
executor_factory.register("tool", PlanToolExecutor)
executor_factory.register("llm", LLMExecutor)

__all__ = [
    "ExecutorRegistry",
    "BaseExecutor",
    "LLMExecutor",
    "ToolExecutor",
    "WorkflowExecutor",
    "ActionResolver",
    "action_resolver",
    "ExecutorContract",
    "ExecutorFactory",
    "executor_factory",
    "ExecutionTarget",
    "PlanToolExecutor",
]
