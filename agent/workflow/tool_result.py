"""ToolResult — 工具执行结果。

工具调用的原始输出（stdout/stderr/exit_code）。
Executor 不创建 Artifact，只返回 ToolResult。
Artifact 由 WorkflowExecutor 根据 ToolResult + Validator 创建。
"""
from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class ToolResult:
    """工具执行结果。
    
    Attributes:
        success: 是否成功
        stdout: 标准输出
        stderr: 标准错误
        exit_code: 退出码
        diagnostics: 诊断信息（执行耗时、内存等）
        raw_output: 完整原始输出
    """
    success: bool = False
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    diagnostics: Optional[Dict] = None
    raw_output: str = ""