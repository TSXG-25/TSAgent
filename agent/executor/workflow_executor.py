"""WorkflowExecutor — 工作流执行引擎。

职责：
- 验证 Prompt 变量都在 Stage.inputs 中声明 (Fix 2)
- 重试逻辑由 WorkflowExecutor 控制 (Fix 3)
- 成功→Validator→Artifact；失败→跳过
"""
import logging
from typing import Any, Dict, Set
from agent.workflow import Workflow, ExecutionContext, Artifact, ExecutionResult
from .executor_registry import ExecutorRegistry
from agent.prompts.workflow import PromptRegistry

logger = logging.getLogger(__name__)


class WorkflowExecutor:
    
    async def execute(
        self,
        workflow: Workflow,
        context: ExecutionContext,
    ) -> ExecutionResult:
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
            
            if stage.required_outputs:
                missing = [r for r in stage.required_outputs if not context.get_artifact(r)]
                if missing:
                    print(f"     ⏭️ 跳过（缺失依赖: {missing}）")
                    continue
            
            # Fix 2: 验证 Prompt 变量都在 stage.inputs 或 stage.arguments 中声明
            template = PromptRegistry.get(workflow.id, stage.id)
            if template and template.variables:
                declared = {i.type for i in (stage.inputs or [])}
                outputs = {o.type for o in (stage.outputs or [])}
                # arguments 中的 artifact 引用也算声明
                artifact_args = {a.artifact for a in (stage.arguments or []) if a.artifact}
                declared_all = declared | outputs | artifact_args
                extra = set(template.variables) - declared_all
                if extra:
                    raise ValueError(
                        f"[{stage.id}] Prompt 使用了未声明的变量: {extra}。"
                        f" 已声明 inputs: {declared}, outputs: {outputs}, "
                        f" arguments: {artifact_args}"
                    )
            
            # 渲染 Prompt（注入 Artifact）
            system, user = PromptRegistry.render_parts(workflow.id, stage.id, context.artifacts)
            prompt = f"{system}\n\n{user}" if system else user
            executor = ExecutorRegistry.get(stage.execution.executor.value)
            
            # Fix 3: retry 由 WorkflowExecutor 控制
            max_retries = stage.execution.max_retries or 0
            exec_result = None
            for attempt in range(max_retries + 1):
                exec_result = await executor.execute(stage=stage, context=context, prompt=prompt)
                if exec_result.success:
                    break
                if attempt < max_retries:
                    print(f"     🔄 重试 ({attempt+1}/{max_retries})")
            
            if exec_result and exec_result.success:
                content = exec_result.text
                if exec_result.tool_result:
                    content = exec_result.tool_result.stdout or content
                
                # Fix: verify_code should keep the original code, not the execution output
                if stage.id == "verify_code":
                    input_code_art = context.get_artifact("python_code")
                    if input_code_art and input_code_art.content:
                        content = input_code_art.content  # keep original code
                        exec_result.outputs["text"] = content
                
                # Fix: strip markdown code blocks from LLM output
                if content:
                    import re
                    content = re.sub(r'^```(?:python)?\s*\n?', '', content, flags=re.MULTILINE)
                    content = re.sub(r'\n?```\s*$', '', content, flags=re.MULTILINE)
                
                for out in (stage.outputs or []):
                    if content and len(content) > 3:
                        artifact = Artifact(
                            id=f"{stage.id}-{idx}", type=out.type,
                            content=content, summary=content[:200], created_by=stage.id,
                        )
                        context.set_artifact(artifact)
                        print(f"     → [{out.type}] {content[:80]}")
                        final_outputs[out.type] = content[:200]
                    else:
                        print(f"     → [{out.type}] (空)")
                
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
                "stage": stage.id, "executor": stage.execution.executor.value,
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
            for e in errors: print(f"  ⚠️ {e}")
        print(f"{'='*50}\n")
        result.outputs["_summary"] = summary
        return result
    
    def _build_summary(self, context: ExecutionContext) -> str:
        sol = context.get_artifact("solution_file")
        if sol and sol.content: return f"已生成解题程序 → output/solution.py"
        code = context.get_artifact("verified_code")
        if code: return f"代码已验证通过"
        code2 = context.get_artifact("python_code")
        if code2: return f"代码已生成"
        q = context.get_artifact("question_text")
        if q: return f"问题已读取: {len(q.content)} 字符"
        return "工作流执行完成"