"""ToolArgument — Stage 参数绑定。

定义 Artifact 或常量如何映射到工具的参数字段。
ToolExecutor 遍历 stage.arguments 构建 params 字典。
"""
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class ToolArgument:
    """工具参数绑定。
    
    artifact: 输入 Artifact 的类型名称（例如 "python_code"）
    param: 工具的参数名（例如 "code"）
    constant: 可选常量值（例如 "output/solution.py"），如果提供则覆盖 artifact
    
    如果 artifact 和 constant 都提供，使用 constant。
    如果都不提供，跳过该参数。
    """
    param: str
    artifact: Optional[str] = None
    constant: Optional[Any] = None