"""ExecutionDispatcher — 已弃用（deprecated）。

执行器选择已由 Compiler（ToolSelector.compile()）接管：
    ExecutionPlan.executor 决定 "tool" | "llm" | "react"。

本模块保留为向后兼容 shim：
- select_executor_name() / should_use_tool_executor() → 调 compiler.compile
- 所有调用都发出 DeprecationWarning
Phase C 结束后物理删除。
"""
import warnings
from typing import Dict, Optional

from agent.task import Task, Verb, ExecutionPlan
from agent.selector.tool_selector import ToolSelector
from agent.selector.rules import DEFAULT_RULES


def _get_compiler() -> ToolSelector:
    """懒加载 Compiler（注册全部默认规则）。"""
    compiler = ToolSelector()
    for rule in DEFAULT_RULES:
        compiler.add_rule(rule)
    return compiler


class ExecutionDispatcher:
    """已弃用。请使用 Compiler.compile() 决定执行器。"""

    def dispatch(self, task: Task, plan: Optional[ExecutionPlan] = None) -> ExecutionPlan:
        """新入口：委托 Compiler.compile()。"""
        warnings.warn(
            "ExecutionDispatcher deprecated, use Compiler.compile()",
            DeprecationWarning,
            stacklevel=2,
        )
        if plan is not None and plan.steps:
            return plan
        compiler = _get_compiler()
        return compiler.compile(task)

    def should_use_tool_executor(self, task: Task, plan: Optional[ExecutionPlan] = None) -> bool:
        """已弃用。"""
        warnings.warn(
            "ExecutionDispatcher deprecated, use Compiler.compile()",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.dispatch(task, plan).executor == "tool"

    def select_executor_name(self, task: Task, plan: Optional[ExecutionPlan] = None) -> str:
        """已弃用。"""
        warnings.warn(
            "ExecutionDispatcher deprecated, use Compiler.compile()",
            DeprecationWarning,
            stacklevel=2,
        )
        ep = self.dispatch(task, plan)
        return "tool" if ep.executor == "tool" else "react"


# 全局单例（保留兼容）
dispatcher = ExecutionDispatcher()