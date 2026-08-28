"""ToolResult — 工具执行结果。

工具调用的机器事实与展示文本分开保存。``value`` 对应 harness 的
canonical tool value；``content`` 是面向模型/用户的投影。失败结果不携带
成功 value。Artifact 仍由上层根据 ToolResult + Validator 创建。
"""
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class ToolResult:
    """工具执行结果。

    Attributes:
        success: 是否成功
        value: 工具产生的机器可消费事实；失败时必须为 None
        content: 面向模型/用户的展示文本，不作为成功事实
        error: 稳定的工具错误摘要
        stdout: 标准输出
        stderr: 标准错误
        exit_code: 退出码
        diagnostics: 诊断信息（执行耗时、内存等）
        raw_output: 完整原始输出
    """
    success: bool = False
    value: Optional[Dict[str, Any]] = None
    content: str = ""
    error: str = ""
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    diagnostics: Optional[Dict] = None
    raw_output: str = ""
