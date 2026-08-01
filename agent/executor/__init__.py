"""Executor — 执行器包。

架构：
- contract: 统一执行器契约（execute(target, context) -> ExecutionResult）+ ExecutorFactory
- executors/: 并列执行器
    - tool.py:     ToolExecutor（确定性 ExecutionPlan 步骤序列）
    - llm.py:      LLMExecutor（纯 LLM 推理）— 暂留在 llm_executor.py，Phase C 迁移
    - react.py:    ReactExecutor（ReAct 循环）
    - workflow.py: WorkflowExecutor（消费整个 Workflow DAG）
- action_resolver: Executor 与 ToolRegistry 之间的隔离层

选择执行器只通过 ExecutorFactory（按 ExecutionPlan.executor_type），
使用方不做 if/elif 选择。旧 Stage 体系（ExecutorRegistry）已在 Phase B.4 移除。
"""
from .contract import (
    Executor as ExecutorContract,
    ExecutorFactory,
    executor_factory,
    ExecutionTarget,
)
from .llm_executor import LLMExecutor
from .executors.tool import ToolExecutor as PlanToolExecutor
from .executors.workflow import WorkflowExecutor as V2WorkflowExecutor
from .executors.react import ReactExecutor
from .action_resolver import ActionResolver, resolver as action_resolver

# 注册统一契约执行器
executor_factory.register("tool", PlanToolExecutor)
executor_factory.register("llm", LLMExecutor)
executor_factory.register("workflow", V2WorkflowExecutor)
# react 尚未注册到 factory：其 execute(state, tasks) 签名是旧契约，
# orchestrator 仍直接实例化 ReactExecutor()（Phase B.3 完成迁移后接入）。

__all__ = [
    "LLMExecutor",
    "PlanToolExecutor",
    "V2WorkflowExecutor",
    "ReactExecutor",
    "ActionResolver",
    "action_resolver",
    "ExecutorContract",
    "ExecutorFactory",
    "executor_factory",
    "ExecutionTarget",
]
