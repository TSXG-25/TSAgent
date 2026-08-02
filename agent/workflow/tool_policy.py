# agent/workflow/tool_policy.py
"""ToolPolicy — 工具访问策略。

定义 Stage 可以访问哪些工具。
WorkflowExecutor 根据 ToolPolicy 限制工具调用。
"""
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class ToolPolicy:
    """工具访问策略。
    
    Attributes:
        allow: 允许的工具名称列表（如 ["read_file", "write_file"]）
        deny: 显式禁止的工具名称列表
        max_calls: 最多调用次数（仅对 REACT executor 有效）
        readonly: 是否为只读模式（禁止所有写操作）
    """
    allow: List[str] = field(default_factory=list)
    deny: Optional[List[str]] = None
    max_calls: int = 1
    readonly: bool = False
    
    def allows(self, tool_name: str) -> bool:
        """检查工具是否被允许。"""
        if self.deny and tool_name in self.deny:
            return False
        if not self.allow:
            return True  # 空列表 = 允许所有
        return tool_name in self.allow