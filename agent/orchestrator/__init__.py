"""Orchestrator — 执行编排层（包）。

Phase C.1：从单文件 orchestrator.py 拆包。
结构：
- main.py:          ExecutionOrchestrator 容器（timings / selector / conversation_state）
- context_builder.py: ContextBuilder（CognitiveContext 构建 + 上下文渲染）
- planner.py:       PlannerStage（plan / replan / task 转换）
- executor.py:      ExecutionStage（execute 分发）
- finalizer.py:     Finalizer（最终答案 + 记忆提交）

Runtime 只做状态机迁移，不直接接触各 Stage。
"""
__all__ = ["ExecutionOrchestrator"]


def __getattr__(name: str):
    if name != "ExecutionOrchestrator":
        raise AttributeError(name)
    from .main import ExecutionOrchestrator

    globals()[name] = ExecutionOrchestrator
    return ExecutionOrchestrator
