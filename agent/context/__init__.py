"""context — 上下文构建与渲染。

- context_service.py: ContextService（LLM Think Prompt 组装，从 services/ 迁入）

Phase C.3：ContextService 从 services/ 迁入（它不属于 Service 层，
是 Executor 的 Prompt 渲染器；依赖方向 context → task / memory / workspace）。
"""
from .context_service import ContextService
from .contracts import (
    RuntimeContext,
    PlannerContext,
    ExecutorContext,
    ReflectionContext,
)

__all__ = [
    "ContextService",
    "RuntimeContext",
    "PlannerContext",
    "ExecutorContext",
    "ReflectionContext",
]
