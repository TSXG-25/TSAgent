# agent/workflow/execution.py
"""ExecutionSpec — Stage 的执行配置。

ExecutionSpec 封装了 Stage 如何执行的所有参数，
从 ExecutorType 到工具策略、超时、温度等。
Stage 本身只描述做什么，ExecutionSpec 描述怎么做。
"""
from dataclasses import dataclass, field
from typing import Optional
from .executor_type import ExecutorType
from .tool_policy import ToolPolicy


@dataclass
class ExecutionSpec:
    """执行配置。
    
    策略模式：Stage 不直接知道自己的执行方式，
    而是通过 ExecutionSpec 委托给对应的 Executor。
    
    Attributes:
        executor: 执行器类型（LLM, TOOL, REACT, PIPELINE）
        tool_policy: 工具访问策略（None = 不允许工具）
        max_retries: 失败重试次数
        timeout: 超时秒数（None = 不限制）
        max_tokens: 最大输出 tokens（仅 LLM/REACT 有效）
        temperature: LLM 温度（None = 使用全局默认）
    """
    executor: ExecutorType
    tool_policy: Optional[ToolPolicy] = None
    max_retries: int = 0
    timeout: Optional[int] = None
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None