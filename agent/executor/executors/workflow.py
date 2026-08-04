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
from typing import Any, Dict, List, Optional

from agent.task import Task, ExecutionPlan
from agent.workflow import Workflow, ExecutionContext, Artifact, ExecutionResult
from agent.prompts.workflow import PromptRegistry
from agent.compiler.tool_selector import Compiler
from agent.compiler.context import CompilerContext
from agent.compiler.rules import DEFAULT_RULES
from agent.executor.contract import executor_factory
from agent.registry.tool_registry import registry as _tool_registry

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
    ) -> ExecutionResult:
        """迭代执行 Workflow 的所有 Stage。

        Stage → Task → Compiler → ExecutionPlan → Executor。
        所有 Stage 能力（validator/retry/artifact）在此编排层保留。
        """
        sorted_stages = workflow.topological_sort()
        total = len(sorted_stages)
        errors = []
        final_outputs: Dict[str, str] = {}

        print(f"\n{'='*50}")
        print(f"🚀 工作流: {workflow.id}")
        print(f"{'='*50}")
        print(f"  阶段: {[s.id for s in sorted_stages]}")
        print(f"{'='*50}\n")

        for idx, stage in enumerate(sorted_stages):
            stage_num = idx + 1
            print(f"  [{stage_num}/{total}] {stage.id} — {stage.description or stage.execution.executor.value}")

            # ── 依赖检查：required_outputs 跳过 ──
            if stage.required_outputs:
                missing = [r for r in stage.required_outputs if not context.get_artifact(r)]
                if missing:
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
            max_retries = task.policy.max_retries or 0
            exec_result = None
            for attempt in range(max_retries + 1):
                exec_result = await self._execute_plan(plan, task, context)
                if exec_result.success:
                    break
                if attempt < max_retries:
                    print(f"     🔄 重试 ({attempt+1}/{max_retries})")

            if exec_result and exec_result.success:
                content = exec_result.text
                content = self._strip_code_blocks(content)

                # Tool stages often validate or persist an input artifact;
                # their useful output is that artifact/path, not stdout such
                # as "syntax check passed" or "file written".
                artifact_content = content
                if "code" in resolved_inputs:
                    artifact_content = str(resolved_inputs["code"])
                elif "content" in resolved_inputs and "path" in resolved_inputs:
                    artifact_content = str(resolved_inputs["path"])

                # ── 回填 Artifact ──
                for out in (stage.outputs or []):
                    if artifact_content and len(str(artifact_content)) > 3:
                        artifact = Artifact(
                            id=f"{stage.id}-{idx}", type=out.type,
                            content=artifact_content, summary=str(artifact_content)[:200], created_by=stage.id,
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
                            v, r = validator_obj.validate({}, {"path": sol_path})
                            if not v:
                                errors.append(f"[{stage.id}] {r}")
                                print(f"     ⚠️ Validator: {r}")
            else:
                err = exec_result.error[:100] if exec_result else "unknown"
                errors.append(f"[{stage.id}] {err}")
                print(f"     ✗ 失败: {err}")

            context.record_action({
                "stage": stage.id,
                "executor": plan.executor,
                "success": exec_result.success if exec_result else False,
            })

        summary = self._build_summary(context)
        result = ExecutionResult(
            success=len(errors) == 0,
            outputs=final_outputs,
            error="; ".join(errors) if errors else "",
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
