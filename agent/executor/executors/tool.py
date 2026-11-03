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
from agent.executor.verifier import ExecutionArtifacts, execution_verifier, snapshot_outputs, verified_file_effects, verified_consumed_files
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
        repository_indexer = context.get_var("repository_indexer")
        t0 = time.time()
        before = snapshot_outputs(target.output_paths, workspace) if target.output_paths else {}
        result = await plan_executor.execute(
            plan,
            workspace=workspace,
            cancellation_view=cancellation_view,
            repository_indexer=repository_indexer,
        )
        after = snapshot_outputs(target.output_paths, workspace) if target.output_paths else {}
        changed = [path for path, digest in after.items() if before.get(path) != digest]
        artifacts = ExecutionArtifacts(
            files_written=[*result.get('_files_written', []), *changed],
            file_operations=list(result.get('_file_operations', [])),
        )
        verified_files = verified_file_effects(artifacts, workspace)
        consumed_files = verified_consumed_files(artifacts, workspace)
        # Declared outputs must actually be produced/changed during this call.
        # A successful command plus a stale existing file is not completion.
        if target.output_paths and not result.get('_error'):
            declared = [workspace.relative_path(workspace.resolve_path(path)) for path in target.output_paths]
            missing = [path for path in declared if not any(
                item['path'] == path or item['path'].startswith(path.rstrip('/') + '/')
                for item in verified_files
            )]
            if missing:
                result['_error'] = 'ARTIFACT_OUTPUT_UNVERIFIED: ' + ', '.join(missing)
                result['_error_code'] = 'ARTIFACT_OUTPUT_UNVERIFIED'
                result['_side_effect_state'] = 'UNKNOWN'

        if result.get("_error"):
            error = str(result["_error"])
            structured_error_code = str(result.get("_error_code", "") or "")
            error_code = structured_error_code or classify_execution_error(error)
            classification_source = (
                ClassificationSource.STRUCTURED
                if structured_error_code
                else ClassificationSource.LEGACY_FALLBACK
            )
            side_effect_state = str(
                result.get("_side_effect_state", "UNKNOWN")
            )
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
                    classification_source=classification_source.value,
                    side_effect_state=side_effect_state,
                ),
                outputs={},
                metadata={
                    "verified_files": verified_files,
                    "consumed_files": consumed_files,
                    "executor": "tool",
                    "task_id": target.id,
                    "tools_called": list(result.get("_tools_called", []) or []),
                    **({"error_code": error_code} if error_code else {}),
                    "classification_source": classification_source.value,
                    "side_effect_state": side_effect_state,
                },
            )

        # ── Verifier 阶段（ADR-0012）：success 只能由 ExecutionVerifier 产生 ──
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
                    side_effect_state="UNKNOWN",
                ),
                outputs={},
                metadata={
                    "verified_files": verified_files,
                    "consumed_files": consumed_files,
                    "executor": "tool",
                    "task_id": target.id,
                    "tools_called": list(result.get("_tools_called", []) or []),
                    "verifier": verification.verifier,
                    "verifier_checks": verification.checks,
                    "side_effect_state": "UNKNOWN",
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
                "verified_files": verified_files,
                "consumed_files": consumed_files,
                "executor": "tool",
                "task_id": target.id,
                "tools_called": list(result.get("_tools_called", []) or []),
                "sources": list(result.get("_sources", []) or []),
                "time_s": round(time.time() - t0, 2),
                "verifier": verification.verifier,
                "verifier_checks": verification.checks,
                # 中间变量（供上层提取 facts）
                "variables": {
                    k: str(v)
                    for k, v in result.items()
                    if not k.startswith("_")
                },
            },
        )
