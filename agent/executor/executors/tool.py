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
from agent.workflow import ExecutionContext, ExecutionResult
from agent.executor.plan_executor import plan_executor
from agent.executor.verifier import ExecutionArtifacts, execution_verifier


class ToolExecutor:
    """确定性步骤执行器（适配 plan_executor 到统一契约）。"""

    async def execute(
        self,
        target: Task,
        context: ExecutionContext,
    ) -> ExecutionResult:
        plan = context.get_var("execution_plan")
        if plan is None:
            return ExecutionResult(
                success=False,
                error="ToolExecutor: context 缺少 execution_plan（需先经 Compiler.compile）",
                metadata={"executor": "tool", "task_id": target.id},
            )

        workspace = context.get_var("workspace")
        t0 = time.time()
        result = await plan_executor.execute(plan, workspace=workspace)

        if result.get("_error"):
            return ExecutionResult(
                success=False,
                error=result["_error"],
                outputs={},
                metadata={"executor": "tool", "task_id": target.id},
            )

        # ── Verifier 阶段（ADR-0012）：success 只能由 ExecutionVerifier 产生 ──
        artifacts = ExecutionArtifacts(
            files_written=list(result.get("_files_written", []) or []),
        )
        verification = execution_verifier.verify(plan, artifacts, task=target)
        if not verification.success:
            return ExecutionResult(
                success=False,
                error=verification.detail,
                outputs={},
                metadata={
                    "executor": "tool",
                    "task_id": target.id,
                    "verifier": verification.verifier,
                    "verifier_checks": verification.checks,
                },
            )

        content = result.get("_last_output", "")
        return ExecutionResult(
            success=True,
            outputs={"text": content},
            metadata={
                "executor": "tool",
                "task_id": target.id,
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
