"""ExecutionResult — 所有 Executor 的统一返回类型。

WorkflowExecutor 不判断 isinstance。
所有 Executor（LLM/Tool/Workflow）都返回 ExecutionResult。
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from agent.action_result import ActionResult
from .artifact import Artifact
from .tool_result import ToolResult


@dataclass
class ExecutionResult:
    """统一执行结果。

    Attributes:
        success: 是否成功
        outputs: 输出内容（LLM 的输出文本 / Tool 的 stdout 等）
        artifacts: 本次执行产出的 Artifact 列表（parents 链溯源）
        tool_result: 工具的 canonical value 与展示投影（仅 ToolExecutor 使用）
        action_result: 本次动作的 provider-neutral 事实投影
        metadata: 元数据（耗时、token 数等）
        error: 错误信息（失败时使用）
        trace_id: 溯源 ID（用于关联执行链路 / 日志）
    """
    success: bool = True
    outputs: Dict[str, str] = field(default_factory=dict)
    artifacts: List[Artifact] = field(default_factory=list)
    tool_result: Optional[ToolResult] = None
    action_result: Optional[ActionResult] = None
    metadata: Optional[Dict] = None
    error: str = ""
    trace_id: Optional[str] = None

    @property
    def text(self) -> str:
        """获取主要的输出文本。"""
        if self.outputs:
            return next(iter(self.outputs.values()))
        return ""
