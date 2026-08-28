"""ToolExecutor — 确定性 ExecutionPlan 执行器。

统一契约：async execute(target, context) -> ExecutionResult
- target: Task（来自 Planner / Stage.to_task 投影）
- context: ExecutionContext（execution_plan 通过 context.set_var("execution_plan", plan) 注入）

内部委托 plan_executor 按序执行 ExecutionStep（变量替换 → 工具调用 → 结果传递）。
不创建 Artifact（由上层 WorkflowExecutor / orchestrator 处理）。

这是并列执行器之一，与 LLMExecutor / WorkflowExecutor 平级。
"""
import time
from typing import Any, Dict, Optional

from agent.task import Task
from agent.action_result import ActionResult
from agent.workflow import ExecutionContext, ExecutionResult, ToolResult
from agent.executor.plan_executor import plan_executor
from agent.executor.verifier import ExecutionArtifacts, execution_verifier
from agent.execution_errors import classify_execution_error
from agent.failure import ClassificationSource


class ToolExecutor:
    """确定性步骤执行器（适配 plan_executor 到统一契约）。"""

    async def execute(
        self,
        target: Task,
        context: ExecutionContext,
    ) -> ExecutionResult:
        plan = context.get_var("execution_plan")
        if plan is None:
            error = "ToolExecutor: context 缺少 execution_plan（需先经 Compiler.compile）"
            return ExecutionResult(
                success=False,
                error=error,
                tool_result=ToolResult(success=False, error=error),
                action_result=ActionResult.failure(
                    error_code="MISSING_EXECUTION_PLAN",
                    content=error,
                    classification_source=ClassificationSource.STRUCTURED.value,
                ),
                metadata={"executor": "tool", "task_id": target.id},
            )

        workspace = context.get_var("workspace")
        cancellation_view = context.get_var("cancellation_view")
        t0 = time.time()
        result = await plan_executor.execute(
            plan,
            workspace=workspace,
            cancellation_view=cancellation_view,
        )

        if result.get("_error"):
            error = str(result["_error"])
            error_code = classify_execution_error(error)
            return ExecutionResult(
                success=False,
                error=error,
                tool_result=ToolResult(
                    success=False,
                    error=error,
                    stderr=error,
                ),
                action_result=ActionResult.failure(
                    error_code=error_code or "TOOL_EXECUTION_FAILED",
                    content=error,
                    classification_source=ClassificationSource.LEGACY_FALLBACK.value,
                ),
                outputs={},
                metadata={
                    "executor": "tool",
                    "task_id": target.id,
                    "tools_called": list(result.get("_tools_called", []) or []),
                    **({"error_code": error_code} if error_code else {}),
                },
            )

        # ── Verifier 阶段（ADR-0012）：success 只能由 ExecutionVerifier 产生 ──
        artifacts = ExecutionArtifacts(
            files_written=list(result.get("_files_written", []) or []),
            file_operations=list(result.get("_file_operations", []) or []),
        )
        verification = execution_verifier.verify(
            plan,
            artifacts,
            task=target,
            workspace=workspace,
        )
        if not verification.success:
            return ExecutionResult(
                success=False,
                error=verification.detail,
                tool_result=ToolResult(
                    success=False,
                    error=verification.detail,
                    stderr=verification.detail,
                ),
                action_result=ActionResult.failure(
                    error_code="ACTION_VERIFICATION_FAILED",
                    content=verification.detail,
                    verified=False,
                    classification_source=ClassificationSource.STRUCTURED.value,
                    retryable=False,
                ),
                outputs={},
                metadata={
                    "executor": "tool",
                    "task_id": target.id,
                    "tools_called": list(result.get("_tools_called", []) or []),
                    "verifier": verification.verifier,
                    "verifier_checks": verification.checks,
                },
            )

        content = result.get("_last_output", "")
        value = {
            key: value
            for key, value in result.items()
            if not key.startswith("_")
        }
        return ExecutionResult(
            success=True,
            outputs={"text": content},
            tool_result=ToolResult(
                success=True,
                value=value,
                content=str(content),
                stdout=str(content),
                raw_output=str(content),
            ),
            action_result=ActionResult.success(
                value=value,
                content=str(content),
                verified=True,
            ),
            metadata={
                "executor": "tool",
                "task_id": target.id,
                "tools_called": list(result.get("_tools_called", []) or []),
                "time_s": round(time.time() - t0, 2),
                "verifier": verification.verifier,
                "verifier_checks": verification.checks,
                # 中间变量（供上层提取 facts）
                "variables": {
                    k: str(v)[:300]
                    for k, v in result.items()
                    if not k.startswith("_")
                },
            },
        )
