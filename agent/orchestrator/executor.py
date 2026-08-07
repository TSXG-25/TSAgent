"""ExecutionStage — EXECUTE 阶段编排。

唯一执行链：Task → ExecutionPlan → ExecutorFactory → ExecutionResult。
"""
import time
from dataclasses import replace
from typing import Tuple

from agent.state import AgentState
from agent.executor.contract import executor_factory
from agent.workflow import ExecutionContext
from agent.task import Task, ExecutionPlan, Verb
from agent.compiler.context import CompilerContext
from agent.registry.tool_registry import registry as _tool_registry
from agent.compat.workspace import get_legacy_workspace_service
from agent.execution_errors import classify_execution_error, is_non_retriable


class ExecutionStage:
    """EXECUTE 阶段执行器。

    持有对 ExecutionOrchestrator 容器的反向引用（访问 _timings）。
    """

    def __init__(self, orchestrator):
        self._orch = orchestrator

    async def run(self, state: AgentState) -> Tuple[AgentState, str]:
        """EXECUTE 阶段：通过 ExecutorFactory 分发执行。

        对于每个 Task，先确保存在 Compiler 产出的 ExecutionPlan，
        再按 ``plan.executor`` 通过 ExecutorFactory 分发。
        """
        t_exec = time.perf_counter()
        tasks = list(state.get("plan") or [])
        execution_plans = list(state.get("execution_plans", []) or [])

        # AgentState is a runtime cache; compile when a plan has not been cached.
        ws_service = None
        try:
            run_context = getattr(self._orch, "run_context", None)
            if run_context is not None:
                if run_context.workspace is not None:
                    ws_service = run_context.workspace
            else:
                ws_service = get_legacy_workspace_service()
        except Exception:
            pass

        for idx, task_dict in enumerate(tasks):
            if (
                str(task_dict.get("verb", "")).lower() == Verb.WRITE.value
                and bool((task_dict.get("inputs") or {}).get("use_prior_facts"))
            ):
                prior_facts: dict[str, object] = {}
                for previous_task in tasks[:idx]:
                    prior_facts.update(previous_task.get("facts", {}) or {})
                if prior_facts:
                    inputs = dict(task_dict.get("inputs") or {})
                    inputs["research_context"] = "\n".join(
                        f"{key}: {str(value)[:1200]}"
                        for key, value in prior_facts.items()
                    )
                    task_dict["inputs"] = inputs
                    if idx < len(execution_plans):
                        execution_plans[idx] = None
            task_obj = Task.from_dict(task_dict)
            plan = execution_plans[idx] if idx < len(execution_plans) else None
            if not isinstance(plan, ExecutionPlan):
                plan = self._orch._selector.compile(
                    task_obj,
                    context=CompilerContext(workspace=ws_service, registry=_tool_registry),
                )
                if idx < len(execution_plans):
                    execution_plans[idx] = plan
                else:
                    execution_plans.append(plan)

            print(f"  🔀 Compiler: {task_dict.get('id', '?')} → {plan.executor}_executor")
            context = ExecutionContext(
                task=task_obj,
                user_input=state.get("messages", [])[-1].content
                if state.get("messages") and hasattr(state["messages"][-1], "content")
                else "",
                facts=dict(task_dict.get("facts", {}) or {}),
                variables={},
            )
            if ws_service:
                context.set_var("workspace", ws_service)
            run_context = getattr(self._orch, "run_context", None)
            if run_context is not None:
                context.set_var("artifact_store", run_context.artifacts)
                context.set_var("event_bus", run_context.event_bus)
            context.set_var("execution_plan", plan)
            context.set_var("conversation_snapshot", state.get("conversation_snapshot"))
            context.set_var("conversation_reference_type", state.get("conversation_reference_type"))
            context.set_var("conversation_runtime_continuation", state.get("conversation_runtime_continuation"))

            try:
                executor = executor_factory.get(plan.executor)
                exec_result = await executor.execute(task_obj, context)
            except Exception as exc:
                exec_result = self._failed_result(plan, exc)

            if exec_result.success:
                verification_error = self._verify_completion(task_obj)
                if verification_error:
                    exec_result = replace(
                        exec_result,
                        success=False,
                        error=verification_error,
                    )

            self._apply_result(task_dict, plan, exec_result)
            error_code = str((exec_result.metadata or {}).get("error_code", ""))
            if not exec_result.success and error_code and is_non_retriable(error_code):
                state["runtime_failure_code"] = error_code
                state["runtime_terminal_status"] = (
                    "BLOCKED"
                    if error_code == "RESEARCH_TOOL_UNAVAILABLE"
                    else "FAILED_TERMINAL"
                )
                # Do not execute unrelated downstream tasks after a
                # deterministic boundary failure.
                break

        state["execution_plans"] = execution_plans

        self._orch._timings["executor"] = round(time.perf_counter() - t_exec, 3)

        # 检查结果
        failed = [t for t in (state.get("plan") or []) if t.get("status") == "failed"]
        if not failed:
            return state, "NEXT_TASK"
        return state, "RECOVER"

    @staticmethod
    def _failed_result(plan: ExecutionPlan, exc: Exception):
        """将路由/执行异常收敛为统一 ExecutionResult。"""
        from agent.workflow import ExecutionResult

        error_code = classify_execution_error(exc)
        return ExecutionResult(
            success=False,
            error=f"{plan.executor} executor failed: {exc}",
            metadata={
                "executor": plan.executor,
                **({"error_code": error_code} if error_code else {}),
            },
        )

    @staticmethod
    def _verify_completion(task: Task) -> str:
        """Verify file mutations before allowing a task to be reported done."""
        if task.verb not in (Verb.WRITE, Verb.MODIFY):
            return ""
        if task.target_type != "file" or not task.target.strip():
            return ""

        try:
            from tools.filesystem import ROOT, _resolve_path

            full = _resolve_path(task.target)
            if not full.is_relative_to(ROOT):
                return f"文件写入未验证：目标超出 workspace 范围: {task.target}"
            if not full.exists() or not full.is_file():
                return f"文件写入未验证：文件不存在: {task.target}"
            if full.stat().st_size == 0:
                return f"文件写入未验证：文件为空: {task.target}"
        except Exception as exc:
            return f"文件写入未验证: {task.target} ({exc})"
        return ""

    @staticmethod
    def _apply_result(task_dict: dict, plan: ExecutionPlan, result) -> None:
        """把统一 ExecutionResult 投影回 AgentState 的 Runtime Cache。"""
        task_dict.setdefault("observations", [])
        metadata = result.metadata or {}
        summary = (result.text or result.error or "")[:300]
        task_dict["status"] = "succeeded" if result.success else "failed"
        task_dict["error"] = "" if result.success else result.error
        task_dict["error_code"] = "" if result.success else str(
            metadata.get("error_code", "")
        )
        task_dict["observations"].append({
            "action": f"{plan.executor}_executor",
            "tool": metadata.get("executor", plan.executor),
            "status": "succeeded" if result.success else "failed",
            "summary": summary,
            "artifact_ids": [a.id for a in result.artifacts],
            "time_s": round(float(metadata.get("time_s", 0) or 0), 2),
        })

        variables = metadata.get("variables")
        if variables:
            task_dict.setdefault("facts", {}).update(variables)
