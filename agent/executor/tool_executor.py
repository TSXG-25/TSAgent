"""ToolExecutor — 单次工具调用执行器。

返回 ExecutionResult（内嵌 ToolResult）。
不创建 Artifact（由 WorkflowExecutor 处理）。
"""
import time
from typing import Any, Dict
from agent.workflow import ExecutionContext, Stage, ExecutionResult, ToolResult
from .executor_registry import BaseExecutor


class ToolExecutor(BaseExecutor):
    """单次工具调用执行器。"""
    
    async def execute(self, stage: Stage, context: ExecutionContext, prompt: str) -> ExecutionResult:
        tool_policy = stage.execution.tool_policy
        if not tool_policy or not tool_policy.allow:
            return ExecutionResult(success=False, error="未指定工具")
        
        tool_name = tool_policy.allow[0]
        from agent.registry.tool_registry import registry
        
        tool_obj = registry.get(tool_name)
        if not tool_obj:
            return ExecutionResult(success=False, error=f"工具 {tool_name} 未注册")
        
        # 从 ToolArgument 构建参数
        params = {}
        for arg in (stage.arguments or []):
            if arg.constant is not None:
                params[arg.param] = arg.constant
            elif arg.artifact:
                art = context.get_artifact(arg.artifact)
                if art:
                    params[arg.param] = art.content if isinstance(art.content, str) else str(art.content)
        
        # 调用工具
        t0 = time.time()
        try:
            if hasattr(tool_obj, 'ainvoke'):
                result = await tool_obj.ainvoke(params)
            else:
                import asyncio
                result = await asyncio.to_thread(tool_obj.invoke, params)
            
            output = result.content if hasattr(result, 'content') else str(result)
            elapsed = time.time() - t0
            
            is_error = output.startswith("Error:") or "错误" in output[:20] or "语法错误" in output[:20]
            
            return ExecutionResult(
                success=not is_error,
                outputs={"text": output},
                tool_result=ToolResult(
                    success=not is_error,
                    stdout=output if not is_error else "",
                    stderr=output if is_error else "",
                    exit_code=1 if is_error else 0,
                    diagnostics={"time_s": round(elapsed, 2)},
                    raw_output=output,
                ),
                metadata={"time_s": round(elapsed, 2), "tool": tool_name},
                error=output if is_error else "",
            )
        except Exception as e:
            elapsed = time.time() - t0
            return ExecutionResult(
                success=False,
                error=str(e),
                tool_result=ToolResult(success=False, stderr=str(e), exit_code=-1),
                metadata={"time_s": round(elapsed, 2), "tool": tool_name},
            )