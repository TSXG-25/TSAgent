"""WorkflowExecutor — 工作流编排器（Task 迭代器）。

Workflow 不是执行体系，而是 Task Generator。
本执行器负责：
1. 按拓扑序迭代 Stage
2. 每个 Stage 投影为统一 Task（stage.to_task()）
3. 通过 Compiler 编译 Task → ExecutionPlan
4. 通过 ExecutorFactory 分发：llm → LLMExecutor / tool → ToolExecutor
5. 保留 Stage 的编排能力：Prompt 渲染、required_outputs 跳过、validator、retry、Artifact 回填

不再拥有独立的 Stage 执行体系（ExecutorRegistry / StageToolExecutor / StageLLMExecutor）。
Phase B.4：从 agent/executor/work 草稿正式化；_execute_plan 统一走 ExecutorFactory。
"""
import logging
import re
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict

from agent.checkpoint import (
    CheckpointRecorder,
    ResumeAction,
    ResumeContext,
    ResumeDisposition,
    RunCheckpoint,
    WorkflowCheckpointRequest,
    checkpoint_result_metadata,
    validate_resume,
)

from agent.task import Task, ExecutionPlan
from agent.workflow import (
    Artifact,
    ExecutionContext,
    ExecutionResult,
    Workflow,
    hydrate_checkpoint_artifacts,
    hydrate_declared_file_inputs,
)
from agent.prompts.workflow import PromptRegistry
from agent.compiler.tool_selector import Compiler
from agent.compiler.context import CompilerContext
from agent.compiler.rules import DEFAULT_RULES
from agent.executor.contract import executor_factory
from agent.registry.tool_registry import registry as _tool_registry
from agent.interruption import (
    CancellationSafetyClass,
    SafeCancellationBoundary,
)

logger = logging.getLogger(__name__)


class WorkflowExecutor:

    def __init__(self):
        self._compiler = Compiler()
        for rule in DEFAULT_RULES:
            self._compiler.add_rule(rule)

    async def execute(
        self,
        workflow: Workflow,
        context: ExecutionContext,
        *,
        checkpoint_request: WorkflowCheckpointRequest | None = None,
    ) -> ExecutionResult:
        """迭代执行 Workflow 的所有 Stage。

        Stage → Task → Compiler → ExecutionPlan → Executor。
        所有 Stage 能力（validator/retry/artifact）在此编排层保留。

        ``checkpoint_request`` 是 v2.2B 的可选运行时接线。启用后，
        WorkflowExecutor 在 Stage 边界记录 Checkpoint，并在恢复前消费
        ResumeValidator 的决定；默认路径不改变既有行为。
        """
        sorted_stages = workflow.topological_sort()
        total = len(sorted_stages)
        errors: list[str] = []
        final_outputs: Dict[str, str] = {}
        recorder: CheckpointRecorder | None = None
        current_checkpoint: RunCheckpoint | None = None
        resume_decision = None
        completed_stage_ids: set[str] = set()
        resume_mode = False
        hydration_diagnostics: list[str] = []

        if checkpoint_request is not None:
            recorder = CheckpointRecorder(checkpoint_request)
            if checkpoint_request.checkpoint is None:
                first_stage = sorted_stages[0] if sorted_stages else None
                current_checkpoint = recorder.start(
                    workflow_id=workflow.id,
                    workflow_version=workflow.version,
                    active_stage_id=first_stage.id if first_stage else "",
                    active_task_id=first_stage.id if first_stage else "",
                    execution_plan=self._workflow_plan_fact(workflow),
                    target_summary=(
                        checkpoint_request.target_summary
                        or workflow.description
                        or workflow.id
                    ),
                    activation_attempt_id=checkpoint_request.activation_attempt_id,
                )
            else:
                resume_mode = True
                hydration = hydrate_checkpoint_artifacts(
                    checkpoint_request.checkpoint.artifacts,
                    context,
                )
                if hydration.mismatched_types:
                    return self._checkpoint_blocked_result(
                        checkpoint_request.checkpoint,
                        "恢复 Artifact digest 不匹配："
                        + ", ".join(hydration.mismatched_types),
                        metadata={
                            "unresolved_resume_diagnostics": [
                                "artifact_digest_mismatch"
                            ],
                            "terminal_outputs_verified": False,
                        },
                    )
                resume_context = self._effective_resume_context(
                    workflow, checkpoint_request
                )
                if resume_context is None:
                    return self._checkpoint_blocked_result(
                        checkpoint_request.checkpoint,
                        "恢复请求缺少 ResumeContext；未进入 Executor。",
                    )
                resume_decision = validate_resume(
                    checkpoint_request.checkpoint,
                    resume_context,
                    external_state_evidence=(
                        checkpoint_request.external_state_evidence or None
                    ),
                    compatibility_registry=checkpoint_request.compatibility_registry,
                )
                if resume_decision.disposition is not ResumeDisposition.ALLOW:
                    return self._checkpoint_blocked_result(
                        checkpoint_request.checkpoint,
                        f"恢复未获准：{resume_decision.reason_code.value}",
                        resume_decision,
                    )
                resume_action = resume_decision.action
                if resume_action not in {
                    ResumeAction.RESUME_EXACT,
                    ResumeAction.REPLAY_FROM_STAGE,
                }:
                    return self._checkpoint_blocked_result(
                        checkpoint_request.checkpoint,
                        "v2.2B 暂不消费恢复动作："
                        f"{resume_action.value if resume_action else 'None'}",
                        resume_decision,
                    )
                current_checkpoint = recorder.resume(resume_decision)
                completed_stage_ids = set(current_checkpoint.completed_stage_ids)

        if resume_mode and current_checkpoint is not None:
            for candidate in sorted_stages:
                declared = hydrate_declared_file_inputs(
                    candidate,
                    current_checkpoint.artifacts,
                    context,
                )
                hydration_diagnostics.extend(
                    f"missing_artifact:{item}"
                    for item in declared.missing_types
                )
                hydration_diagnostics.extend(
                    f"artifact_digest_mismatch:{item}"
                    for item in declared.mismatched_types
                )

        print(f"\n{'='*50}")
        print(f"🚀 工作流: {workflow.id}")
        print(f"{'='*50}")
        print(f"  阶段: {[s.id for s in sorted_stages]}")
        print(f"{'='*50}\n")

        for idx, stage in enumerate(sorted_stages):
            cancellation_view = context.get_var("cancellation_view")
            if cancellation_view is not None:
                cancellation_view.raise_if_requested(
                    SafeCancellationBoundary.BEFORE_WORKFLOW_ACTIVATION,
                    CancellationSafetyClass.BOUNDARY_ONLY,
                )
            stage_num = idx + 1
            print(f"  [{stage_num}/{total}] {stage.id} — {stage.description or stage.execution.executor.value}")

            if stage.id in completed_stage_ids:
                print("     ⏭️ Checkpoint 已确认完成，跳过执行")
                continue

            # ── 依赖检查：required_outputs 跳过 ──
            if stage.required_outputs:
                missing = [r for r in stage.required_outputs if not context.get_artifact(r)]
                if missing:
                    if resume_mode and recorder is not None and current_checkpoint is not None:
                        return self._resume_dependency_blocked_result(
                            workflow,
                            stage,
                            missing,
                            recorder,
                            current_checkpoint,
                            context,
                            resume_decision,
                            hydration_diagnostics,
                        )
                    print(f"     ⏭️ 跳过（缺失依赖: {missing}）")
                    continue

            # ── 渲染 Prompt（注入 Artifact）──
            system, user = PromptRegistry.render_parts(workflow.id, stage.id, context.artifacts)
            goal = f"{system}\n\n{user}" if system else (user or stage.description or stage.id)

            # ── Stage → Task 投影 ──
            task = stage.to_task(goal=goal)

            # Resolve Stage.arguments once, then compile the canonical Task.
            # The old executor ignored arguments entirely, which caused the
            # generated answer to be used as a file path and dropped content.
            resolved_inputs = {}
            for param, binding in task.inputs.items():
                if not isinstance(binding, dict):
                    resolved_inputs[param] = binding
                    continue
                if "constant" in binding:
                    resolved_inputs[param] = binding["constant"]
                    continue
                artifact_type = binding.get("artifact")
                artifact = context.get_artifact(artifact_type) if artifact_type else None
                if artifact is None:
                    if resume_mode and recorder is not None and current_checkpoint is not None:
                        return self._resume_dependency_blocked_result(
                            workflow,
                            stage,
                            [str(artifact_type or param)],
                            recorder,
                            current_checkpoint,
                            context,
                            resume_decision,
                            hydration_diagnostics,
                        )
                    raise ValueError(f"阶段 {stage.id} 缺少输入产物: {artifact_type}")
                resolved_inputs[param] = artifact.content

            task = task.model_copy(update={
                "inputs": resolved_inputs,
                "target": str(resolved_inputs.get("path", task.target)),
            })

            # ── Compiler 编译 → 决定 executor ──
            plan = self._compiler.compile(
                task,
                context=CompilerContext(
                    workspace=context.get_var("workspace"),
                    registry=_tool_registry,
                ),
            )

            # ── 执行（retry 由本编排层控制）──
            recovery = self._recover_committed_file_effect(
                task,
                resolved_inputs,
                context=context,
                resume_mode=resume_mode,
            )
            if recovery[0] == "MISMATCH":
                if recorder is not None and current_checkpoint is not None:
                    return self._resume_side_effect_blocked_result(
                        workflow,
                        stage,
                        recovery[1],
                        recorder,
                        current_checkpoint,
                        context,
                        resume_decision,
                        hydration_diagnostics,
                    )
                raise RuntimeError(recovery[1])
            if recovery[0] == "COMMITTED":
                exec_result = ExecutionResult(
                    success=True,
                    outputs={"text": f"reconciled existing file: {recovery[1]}"},
                    metadata={
                        "executor": "resume_reconciler",
                        "side_effect_state": "COMMITTED",
                        "external_reference": recovery[1],
                        "resume_reconciled": True,
                    },
                )
            else:
                max_retries = task.policy.max_retries or 0
                exec_result = None
                for attempt in range(max_retries + 1):
                    exec_result = await self._execute_plan(plan, task, context)
                    if exec_result.success:
                        break
                    if attempt < max_retries:
                        print(f"     🔄 重试 ({attempt+1}/{max_retries})")

            stage_checkpoint_success = bool(exec_result and exec_result.success)
            stage_error = (
                exec_result.error[:100]
                if exec_result and not exec_result.success
                else ""
            )
            if stage_checkpoint_success and exec_result is not None:
                content = exec_result.text
                content = self._strip_code_blocks(content)

                # Tool stages often validate or persist an input artifact;
                # their useful output is that artifact/path, not stdout such
                # as "syntax check passed" or "file written".
                artifact_content = content
                if "code" in resolved_inputs:
                    artifact_content = str(resolved_inputs["code"])
                elif "content" in resolved_inputs and "path" in resolved_inputs:
                    artifact_content = str(resolved_inputs["content"])

                artifact_reference = str(resolved_inputs.get("path", ""))
                storage_reference = artifact_reference
                scoped_workspace = context.get_var("workspace")
                if storage_reference and scoped_workspace is not None:
                    try:
                        storage_reference = scoped_workspace.artifact_reference(
                            storage_reference
                        )
                    except (OSError, ValueError, PermissionError):
                        # Non-file/external references remain opaque values.
                        storage_reference = artifact_reference
                if artifact_reference:
                    try:
                        if scoped_workspace is not None:
                            artifact_content = scoped_workspace.read_text(
                                artifact_reference
                            )
                        else:
                            # Unscoped workflow callers retain the old
                            # compatibility behavior; RunContext paths never
                            # enter this branch.
                            artifact_content = Path(artifact_reference).read_text(
                                encoding="utf-8"
                            )
                    except (OSError, UnicodeError, ValueError, PermissionError):
                        # A non-file reference (for example an external URI)
                        # keeps the executor output as the logical artifact.
                        pass

                # ── 回填 Artifact ──
                for out in (stage.outputs or []):
                    if artifact_content and len(str(artifact_content)) > 3:
                        artifact = Artifact(
                            id=f"{stage.id}-{idx}", type=out.type,
                            content=artifact_content,
                            summary=str(artifact_content)[:200],
                            storage_uri=storage_reference,
                            metadata={
                                "output_name": out.type,
                                "artifact_type": out.type,
                                "reference": storage_reference,
                                "encoding": "utf-8",
                                "producer_stage_id": stage.id,
                                "producer_task_id": task.id,
                            },
                            created_by=stage.id,
                        )
                        context.set_artifact(artifact)
                        print(f"     → [{out.type}] {str(artifact_content)[:80]}")
                        final_outputs[out.type] = str(artifact_content)[:200]
                    else:
                        print(f"     → [{out.type}] (空)")

                # ── Validator ──
                if stage.validators:
                    for validator_obj in stage.validators:
                        if hasattr(validator_obj, 'validate'):
                            sol_art = context.get_artifact("solution_file")
                            sol_path = sol_art.content if sol_art else ""
                            v, r = validator_obj.validate(
                                {},
                                {
                                    "path": sol_path,
                                    "_workspace": context.get_var("workspace"),
                                },
                            )
                            if not v:
                                stage_checkpoint_success = False
                                stage_error = f"[{stage.id}] {r}"
                                errors.append(f"[{stage.id}] {r}")
                                print(f"     ⚠️ Validator: {r}")
            else:
                err = stage_error or "unknown"
                stage_error = err
                errors.append(f"[{stage.id}] {err}")
                print(f"     ✗ 失败: {err}")

            context.record_action({
                "stage": stage.id,
                "executor": plan.executor,
                "success": stage_checkpoint_success,
            })

            if recorder is not None:
                completed_for_next = set(completed_stage_ids)
                if stage_checkpoint_success:
                    completed_for_next.add(stage.id)
                next_stage = next(
                    (
                        candidate
                        for candidate in sorted_stages[idx + 1:]
                        if candidate.id not in completed_for_next
                    ),
                    None,
                )
                current_checkpoint = recorder.record_stage(
                    stage_id=stage.id,
                    task_id=task.id,
                    execution_plan=plan.to_dict(),
                    success=stage_checkpoint_success,
                    result_error=stage_error,
                    result_metadata=exec_result.metadata if exec_result else {},
                    task_verb=task.verb.value,
                    next_stage_id=next_stage.id if next_stage else "",
                    next_task_id=next_stage.id if next_stage else "",
                    artifacts=context.artifacts.values(),
                    target_summary=(
                        checkpoint_request.target_summary
                        if checkpoint_request else ""
                    ),
                )
                if not stage_checkpoint_success:
                    summary = self._build_summary(context)
                    result = ExecutionResult(
                        success=False,
                        outputs=final_outputs,
                        error=stage_error or "Workflow Stage 执行失败",
                        metadata=checkpoint_result_metadata(
                            current_checkpoint, resume_decision
                        ),
                    )
                    result.outputs["_summary"] = summary
                    return result
                completed_stage_ids.add(stage.id)
                if (
                    checkpoint_request is not None
                    and checkpoint_request.interrupt_after_stage_id == stage.id
                ):
                    current_checkpoint = recorder.suspend()
                    summary = self._build_summary(context)
                    result = ExecutionResult(
                        success=False,
                        outputs=final_outputs,
                        error=f"Workflow 在 Stage {stage.id} 后暂停",
                        metadata=checkpoint_result_metadata(
                            current_checkpoint, resume_decision
                        ),
                    )
                    result.outputs["_summary"] = summary
                    return result

        terminal_outputs = self._terminal_output_types(workflow, sorted_stages)
        missing_terminal_outputs = [
            output_type
            for output_type in terminal_outputs
            if context.get_artifact(output_type) is None
        ]
        if missing_terminal_outputs:
            summary = self._build_summary(context)
            diagnostic = (
                "Workflow terminal outputs 未验证："
                + ", ".join(missing_terminal_outputs)
            )
            metadata = checkpoint_result_metadata(
                current_checkpoint, resume_decision
            )
            metadata.update({
                "terminal_outputs_verified": False,
                "unresolved_resume_diagnostics": list(
                    dict.fromkeys((*hydration_diagnostics, diagnostic))
                ),
            })
            result = ExecutionResult(
                success=False,
                outputs=final_outputs,
                error=diagnostic,
                metadata=metadata,
            )
            result.outputs["_summary"] = summary
            return result

        summary = self._build_summary(context)
        if recorder is not None and current_checkpoint is not None:
            current_checkpoint = recorder.complete(
                artifacts=context.artifacts.values(),
                summary=summary,
            )
        metadata = checkpoint_result_metadata(current_checkpoint, resume_decision)
        metadata.update({
            "terminal_outputs_verified": True,
            "unresolved_resume_diagnostics": hydration_diagnostics,
        })
        result = ExecutionResult(
            success=len(errors) == 0,
            outputs=final_outputs,
            error="; ".join(errors) if errors else "",
            metadata=metadata,
        )
        print(f"\n{'='*50}")
        print(f"{'✅' if result.success else '⚠️'} 工作流完成: {workflow.id}")
        print(f"  {summary}")
        if errors:
            for e in errors:
                print(f"  ⚠️ {e}")
        print(f"{'='*50}\n")
        result.outputs["_summary"] = summary
        return result

    @staticmethod
    def _workflow_plan_fact(workflow: Workflow) -> dict[str, Any]:
        """Build a JSON-only Workflow fact for the initial Checkpoint."""
        return {
            "workflow_id": workflow.id,
            "workflow_version": workflow.version,
            "stages": [
                {
                    "id": stage.id,
                    "depends": list(stage.depends or []),
                    "executor": stage.execution.executor.value,
                    "idempotent": bool(stage.idempotent),
                }
                for stage in workflow.stages
            ],
        }

    @staticmethod
    def _terminal_output_types(workflow: Workflow, sorted_stages) -> tuple[str, ...]:
        dag = workflow._build_dag()
        non_terminal = {
            dependency
            for dependencies in dag.values()
            for dependency in dependencies
        }
        outputs = [
            output.type
            for stage in sorted_stages
            if stage.id not in non_terminal
            for output in (stage.outputs or [])
        ]
        return tuple(dict.fromkeys(outputs))

    @staticmethod
    def _effective_resume_context(
        workflow: Workflow,
        request: WorkflowCheckpointRequest,
    ) -> ResumeContext | None:
        context = request.resume_context
        checkpoint = request.checkpoint
        if context is None or checkpoint is None:
            return context
        if context.requested_action is not ResumeAction.REPLAY_FROM_STAGE:
            return context
        stage = workflow.get_stage(checkpoint.active_stage_id)
        if stage is None:
            return context
        # Stage declaration is the current fact; callers cannot override it
        # with a stale boolean in ResumeContext.
        return replace(
            context,
            stage_idempotent=stage.idempotent,
            requested_stage_id=context.requested_stage_id or stage.id,
        )

    @staticmethod
    def _checkpoint_blocked_result(
        checkpoint: RunCheckpoint,
        error: str,
        decision: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> ExecutionResult:
        """Return a visible non-execution result for an unsafe resume."""
        result_metadata = checkpoint_result_metadata(checkpoint, decision)
        if metadata:
            result_metadata.update(metadata)
        return ExecutionResult(
            success=False,
            error=error,
            metadata=result_metadata,
        )

    def _resume_dependency_blocked_result(
        self,
        workflow: Workflow,
        stage,
        missing: list[str],
        recorder: CheckpointRecorder,
        current_checkpoint: RunCheckpoint,
        context: ExecutionContext,
        resume_decision: Any,
        hydration_diagnostics: list[str],
    ) -> ExecutionResult:
        """Fail visibly when a resumed stage cannot reconstruct its inputs."""
        task = stage.to_task(goal=stage.description or stage.id)
        diagnostic = (
            f"恢复上下文缺少 Stage {stage.id} 的输入产物: {', '.join(missing)}"
        )
        checkpoint = recorder.record_stage(
            stage_id=stage.id,
            task_id=task.id,
            execution_plan=self._workflow_plan_fact(workflow),
            success=False,
            result_error=diagnostic,
            result_metadata={
                "executor": "workflow_resume",
                "side_effect_state": "NONE",
                "resume_diagnostic": diagnostic,
            },
            task_verb=task.verb.value,
            next_stage_id=stage.id,
            next_task_id=stage.id,
            artifacts=context.artifacts.values(),
            target_summary=current_checkpoint.target_summary,
        )
        diagnostics = list(dict.fromkeys((*hydration_diagnostics, diagnostic)))
        return ExecutionResult(
            success=False,
            error=diagnostic,
            metadata={
                **checkpoint_result_metadata(checkpoint, resume_decision),
                "unresolved_resume_diagnostics": diagnostics,
                "terminal_outputs_verified": False,
            },
        )

    @staticmethod
    def _recover_committed_file_effect(
        task: Task,
        resolved_inputs: dict[str, Any],
        *,
        context: ExecutionContext,
        resume_mode: bool,
    ) -> tuple[str, str]:
        """Reconcile a file write that committed before its checkpoint.

        The stage must explicitly declare both ``path`` and ``content``.  A
        matching file is treated as an already-committed idempotent effect;
        an existing but different file is unsafe and must not be overwritten.
        """
        if not resume_mode:
            return "NONE", ""
        verb = str(getattr(task.verb, "value", task.verb)).lower()
        if verb not in {"write", "modify"}:
            return "NONE", ""
        path_value = resolved_inputs.get("path")
        if path_value is None or "content" not in resolved_inputs:
            return "NONE", ""
        workspace = context.get_var("workspace")
        if workspace is not None and hasattr(workspace, "resolve_path"):
            try:
                path = workspace.resolve_path(str(path_value), must_exist=False)
            except (OSError, ValueError) as exc:
                return "MISMATCH", f"恢复副作用目标不属于当前 Run workspace: {exc}"
        else:
            # Legacy non-Service workflows may still supply an absolute path.
            # Production AgentService execution always binds a scoped workspace.
            path = Path(str(path_value))
        if not path.exists():
            return "NONE", str(path)
        if not path.is_file():
            return "MISMATCH", f"恢复副作用目标不是文件: {path}"
        try:
            actual = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            return "MISMATCH", f"无法验证已存在的副作用文件 {path}: {exc}"
        expected = str(resolved_inputs["content"])
        if actual == expected:
            return "COMMITTED", str(path)
        return "MISMATCH", f"副作用文件内容与恢复输入不一致: {path}"

    def _resume_side_effect_blocked_result(
        self,
        workflow: Workflow,
        stage,
        diagnostic: str,
        recorder: CheckpointRecorder,
        current_checkpoint: RunCheckpoint,
        context: ExecutionContext,
        resume_decision: Any,
        hydration_diagnostics: list[str],
    ) -> ExecutionResult:
        task = stage.to_task(goal=stage.description or stage.id)
        checkpoint = recorder.record_stage(
            stage_id=stage.id,
            task_id=task.id,
            execution_plan=self._workflow_plan_fact(workflow),
            success=False,
            result_error=diagnostic,
            result_metadata={
                "executor": "workflow_resume",
                "side_effect_state": "UNKNOWN",
                "resume_diagnostic": diagnostic,
            },
            task_verb=task.verb.value,
            next_stage_id=stage.id,
            next_task_id=stage.id,
            artifacts=context.artifacts.values(),
            target_summary=current_checkpoint.target_summary,
        )
        diagnostics = list(dict.fromkeys((*hydration_diagnostics, diagnostic)))
        return ExecutionResult(
            success=False,
            error=diagnostic,
            metadata={
                **checkpoint_result_metadata(checkpoint, resume_decision),
                "unresolved_resume_diagnostics": diagnostics,
                "terminal_outputs_verified": False,
            },
        )

    # ── 统一执行分发（ExecutorFactory，无 if/elif 选择逻辑）──

    async def _execute_plan(
        self,
        plan: ExecutionPlan,
        task: Task,
        context: ExecutionContext,
    ) -> ExecutionResult:
        """按 plan.executor 通过 ExecutorFactory 分发。"""
        context.set_var("execution_plan", plan)
        executor = executor_factory.get(plan.executor)
        return await executor.execute(task, context)

    # ── 辅助 ──

    @staticmethod
    def _strip_code_blocks(content: str) -> str:
        if not content:
            return content
        content = re.sub(r'^```(?:python)?\s*\n?', '', content, flags=re.MULTILINE)
        content = re.sub(r'\n?```\s*$', '', content, flags=re.MULTILINE)
        return content

    def _build_summary(self, context: ExecutionContext) -> str:
        sol = context.get_artifact("solution_file")
        if sol and sol.content:
            return f"已生成解题程序 → {sol.content}"
        code = context.get_artifact("verified_code")
        if code:
            return f"代码已验证通过"
        code2 = context.get_artifact("python_code")
        if code2:
            return f"代码已生成"
        q = context.get_artifact("question_text")
        if q:
            return f"问题已读取: {len(q.content)} 字符"
        return "工作流执行完成"
