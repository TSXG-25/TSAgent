"""CompilerContext — Compiler 的唯一环境输入。

ADR-0002: compile(task, context) -> ExecutionPlan。
Compiler 永远只有一个输入 (Task, CompilerContext)，
不通过 **services 参数膨胀传入 workspace/memory/repository/...

workspace/repository 可以为空表示能力不可用；registry 为空时由 Compiler
绑定应用级 ToolRegistry，不能静默跳过工具存在性检查。
"""
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class CompilerContext:
    """编译期环境引用（全部可选）。"""
    workspace: Optional[Any] = None      # WorkspaceService / Workspace
    registry: Optional[Any] = None       # ToolRegistry（static check 用）
    repository: Optional[Any] = None     # RepositoryService（search 用）

    # 预留：memory / knowledge / llm 等环境能力
    extra: dict = field(default_factory=dict)

    def get_workspace(self):
        return self.workspace

    def get_registry(self):
        return self.registry
