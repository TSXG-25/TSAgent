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
from agent.execution_errors import classify_execution_error, is_non_retriable
from agent.failure import (
    ClassificationSource,
    FailureKind,
    failure_fact,
)
from agent.effect_truth import record_effect_result, record_execution_evidence
from agent.inbox import AgentInbox
from agent.next_action import NextAction
from agent.action_result import ActionResult
from agent.cognition.effect_authorization import EffectAuthorization
from agent.runtime_gates import has_fresh_evidence
from agent.interruption import (
    CancellationSafetyClass,
    SafeCancellationBoundary,
)


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
        user_input = (
            state.get("messages", [])[-1].content
            if state.get("messages") and hasattr(state["messages"][-1], "content")
            else ""
        )
        authorization = EffectAuthorization.from_request(user_input)

        # AgentState is a runtime cache; compile when a plan has not been cached.
        run_context = getattr(self._orch, "run_context", None)
        ws_service = (
            run_context.workspace
            if run_context is not None
            else None
        )

        # Compile and authorize the complete plan before the first effect.
        # A request such as write→execute intentionally satisfies two
        # outcomes across two tasks; checking each task independently would
        # reject the final task after an earlier effect already happened.
        while len(execution_plans) < len(tasks):
            execution_plans.append(None)
        for idx, task_dict in enumerate(tasks):
            if isinstance(execution_plans[idx], ExecutionPlan):
                continue
            task_obj = Task.from_dict(task_dict)
            execution_plans[idx] = self._orch._selector.compile(
                task_obj,
                context=CompilerContext(workspace=ws_service, registry=_tool_registry),
            )

        preflight_error = next(
            (
                error
                for task in tasks
                for error in (authorization.validate_task(task),)
                if error
            ),
            None,
        )
        if preflight_error:
            failed_index = next(
                index
                for index, task in enumerate(tasks)
                if authorization.validate_task(task)
            )
            failed_plan = execution_plans[failed_index]
            if not isinstance(failed_plan, ExecutionPlan):
                failed_plan = ExecutionPlan(
                    task=Task.from_dict(tasks[failed_index]),
                    steps=[],
                    executor="tool",
                )
            self._apply_result(
                tasks[failed_index],
                failed_plan,
                self._contract_failure_result(
                    failed_plan,
                    preflight_error,
                    "EFFECT_SCOPE_VIOLATION",
                ),
            )
            state["runtime_failure_code"] = "EFFECT_SCOPE_VIOLATION"
            state["runtime_terminal_status"] = "BLOCKED"
            state["execution_plans"] = execution_plans
            return state, "FAIL"

        route_error = authorization.validate_plan_set(execution_plans)
        if route_error:
            failed_plan = execution_plans[0] if execution_plans else None
            if isinstance(failed_plan, ExecutionPlan):
                self._apply_result(
                    tasks[0],
                    failed_plan,
                    self._contract_failure_result(
                        failed_plan,
                        route_error,
                        "EXECUTION_REQUIRED",
                    ),
                )
            state["runtime_failure_code"] = "EXECUTION_REQUIRED"
            state["runtime_terminal_status"] = "BLOCKED"
            state["execution_plans"] = execution_plans
            return state, "FAIL"

        result_driven = bool(state.get("execution_mode") == "result_driven")
        if result_driven:
            action_indexes = [
                index
                for index, task in enumerate(tasks)
                if str(task.get("status", "pending"))
                not in {"succeeded", "skipped"}
            ][:1]
        else:
            action_indexes = list(range(len(tasks)))

        for idx in action_indexes:
            task_dict = tasks[idx]
            run_context = getattr(self._orch, "run_context", None)
            cancellation_view = (
                getattr(run_context, "cancellation_view", None)
                if run_context is not None
                else None
            )
            if cancellation_view is not None:
                cancellation_view.raise_if_requested(
                    SafeCancellationBoundary.BEFORE_TOOL,
                    CancellationSafetyClass.BOUNDARY_ONLY,
                )
            if (
                str(task_dict.get("verb", "")).lower() == Verb.WRITE.value
                and bool((task_dict.get("inputs") or {}).get("use_prior_facts"))
            ):
                prior_fact_lines: list[str] = []
                for previous_task in tasks[:idx]:
                    previous_id = str(previous_task.get("id", "prior-task"))
                    for key, value in (previous_task.get("facts", {}) or {}).items():
                        prior_fact_lines.append(
                            f"[{previous_id}.{key}] {str(value)[:2400]}"
                        )
                if prior_fact_lines:
                    inputs = dict(task_dict.get("inputs") or {})
                    inputs["research_context"] = "\n\n".join(prior_fact_lines)
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
                user_input=user_input,
                facts=dict(task_dict.get("facts", {}) or {}),
                variables={},
            )
            if ws_service:
                context.set_var("workspace", ws_service)
            run_context = getattr(self._orch, "run_context", None)
            if run_context is not None:
                context.set_var("artifact_store", run_context.artifacts)
                context.set_var("event_bus", run_context.event_bus)
                context.set_var("cancellation_view", run_context.cancellation_view)
            context.set_var("execution_plan", plan)
            context.set_var("conversation_snapshot", state.get("conversation_snapshot"))
            context.set_var("conversation_reference_type", state.get("conversation_reference_type"))
            context.set_var("conversation_runtime_continuation", state.get("conversation_runtime_continuation"))

            try:
                executor = executor_factory.get(plan.executor)
                exec_result = await executor.execute(task_obj, context)
            except Exception as exc:
                exec_result = self._failed_result(plan, exc)

            exec_result = self._ensure_action_result(exec_result, plan)

            if exec_result.success:
                verification_error = self._verify_completion(task_obj, ws_service)
                if verification_error:
                    exec_result = replace(
                        exec_result,
                        success=False,
                        error=verification_error,
                        action_result=ActionResult.failure(
                            error_code="ACTION_VERIFICATION_FAILED",
                            content=verification_error,
                            classification_source=ClassificationSource.STRUCTURED.value,
                            retryable=False,
                        ),
                        metadata={
                            **dict(exec_result.metadata or {}),
                            "error_code": "ACTION_VERIFICATION_FAILED",
                            "failed_component": "completion_verifier",
                            "retryable": False,
                        },
                    )

            action_result = getattr(exec_result, "action_result", None)
            if action_result is not None and not action_result.ok:
                metadata = dict(exec_result.metadata or {})
                error_code = str(
                    action_result.error_code
                    or metadata.get("error_code", "ACTION_EXECUTION_FAILED")
                )
                source = str(
                    action_result.classification_source
                    or metadata.get(
                        "classification_source",
                        ClassificationSource.STRUCTURED.value,
                    )
                )
                fact = failure_fact(
                    error_code,
                    message=action_result.content or exec_result.error,
                    component=str(metadata.get("failed_component", plan.executor)),
                    classification_source=ClassificationSource(source),
                    retryable=action_result.retryable,
                )
                metadata.update({
                    "error_code": fact.code,
                    "failure_kind": fact.kind.value,
                    "classification_source": fact.classification_source.value,
                    "retryable": fact.retryable,
                })
                exec_result = replace(exec_result, metadata=metadata)
                state["runtime_failure"] = fact.to_dict()
                state["runtime_failure_kind"] = fact.kind.value
                state["runtime_failure_source"] = fact.classification_source.value
                state["runtime_failure_retryable"] = fact.retryable

            self._apply_result(task_dict, plan, exec_result)
            record_effect_result(state, task_dict, plan, exec_result)
            record_execution_evidence(state, plan, exec_result)
            inbox = AgentInbox.from_dict(state.get("inbox"))
            next_action = NextAction.tool_call(
                plan.executor,
                task_id=str(task_dict.get("id", "")),
                reason=str(task_dict.get("goal", "") or task_dict.get("description", "")),
            )
            action_result = getattr(exec_result, "action_result", None)
            if action_result is not None:
                inbox.add_step({
                    "task_id": str(task_dict.get("id", "")),
                    "tool": str((exec_result.metadata or {}).get("executor", plan.executor)),
                    "action": next_action.to_dict(),
                    "result": action_result.to_dict(),
                })
            else:
                inbox.add_step({
                    "task_id": str(task_dict.get("id", "")),
                    "tool": plan.executor,
                    "action": next_action.to_dict(),
                    "result": {
                        "ok": bool(exec_result.success),
                        "content": exec_result.text or exec_result.error,
                        "error_code": str((exec_result.metadata or {}).get("error_code", "")),
                        "verified": bool(exec_result.success),
                    },
                })
            state["inbox"] = inbox.to_dict()
            state["fresh_evidence"] = has_fresh_evidence(state)
            error_code = str((exec_result.metadata or {}).get("error_code", ""))
            if (
                not exec_result.success
                and str(state.get("runtime_failure_kind", ""))
                == FailureKind.STRUCTURAL.value
            ):
                state["runtime_failure_class"] = "structural"
                break
            if not exec_result.success and error_code and is_non_retriable(error_code):
                state["runtime_failure_code"] = error_code
                state["runtime_terminal_status"] = (
                    "BLOCKED"
                    if error_code in {
                        "RESEARCH_TOOL_UNAVAILABLE",
                        "UNSUPPORTED_CAPABILITY",
                    }
                    else "FAILED_TERMINAL"
                )
                # Do not execute unrelated downstream tasks after a
                # deterministic boundary failure.
                break

        state["execution_plans"] = execution_plans

        self._orch._timings["executor"] = round(time.perf_counter() - t_exec, 3)

        # 检查结果
        failed = [t for t in (state.get("plan") or []) if t.get("status") == "failed"]
        if state.get("runtime_terminal_status") in {"BLOCKED", "FAILED_TERMINAL"}:
            return state, "FAIL"
        if state.get("runtime_failure_kind") == FailureKind.STRUCTURAL.value:
            return state, "RECOVER"
        if not failed:
            if result_driven and any(
                str(task.get("status", "pending"))
                not in {"succeeded", "skipped"}
                for task in tasks
            ):
                state["current_task_index"] = next(
                    index
                    for index, task in enumerate(tasks)
                    if str(task.get("status", "pending"))
                    not in {"succeeded", "skipped"}
                )
                return state, "NEXT_ACTION"
            state["current_task_index"] = len(tasks)
            return state, "NEXT_TASK"
        if result_driven and not state.get("runtime_failure_code"):
            # A normal action failure is an observation for the next
            # reasoning round.  Only non-retriable boundary errors above
            # become a terminal Runtime failure here.
            return state, "OBSERVE"
        return state, "RECOVER"

    @staticmethod
    def _failed_result(plan: ExecutionPlan, exc: Exception):
        """将路由/执行异常收敛为统一 ExecutionResult。"""
        from agent.workflow import ExecutionResult

        error_code = classify_execution_error(exc)
        normalized_code = error_code or "ACTION_EXECUTION_FAILED"
        failure = failure_fact(
            normalized_code,
            message=str(exc) or type(exc).__name__,
            component=f"{plan.executor}_executor",
            classification_source=ClassificationSource.LEGACY_FALLBACK,
        )
        return ExecutionResult(
            success=False,
            error=f"{plan.executor} executor failed: {exc}",
            action_result=ActionResult.failure(
                error_code=normalized_code,
                content=str(exc) or type(exc).__name__,
                failure=failure,
            ),
            metadata={
                "executor": plan.executor,
                "error_code": normalized_code,
                "failure_kind": failure.kind.value,
                "classification_source": failure.classification_source.value,
                "retryable": failure.retryable,
            },
        )

    @staticmethod
    def _contract_failure_result(
        plan: ExecutionPlan,
        error: str,
        error_code: str,
    ):
        """Create a deterministic pre-execution contract failure."""
        from agent.workflow import ExecutionResult

        return ExecutionResult(
            success=False,
            error=error,
            action_result=ActionResult.failure(
                error_code=error_code,
                content=error,
                classification_source=ClassificationSource.STRUCTURED.value,
            ),
            metadata={
                "executor": plan.executor,
                "error_code": error_code,
                "failed_component": "effect_authorization",
                "tools_called": [],
            },
        )

    @staticmethod
    def _ensure_action_result(result, plan: ExecutionPlan):
        """Normalize legacy executors at the single action boundary.

        Tool and LLM executors construct a precise ActionResult themselves.
        Composite/legacy executors still return ExecutionResult, so this
        adapter preserves one canonical observation shape without letting a
        display string become an effect claim.
        """
        if result.action_result is not None:
            return result
        metadata = result.metadata or {}
        if result.success:
            verified = metadata.get("verified")
            if verified is None and metadata.get("verifier"):
                verified = True
            action_result = ActionResult.success(
                value=dict(result.outputs or {}),
                content=result.text,
                verified=verified,
            )
        else:
            action_result = ActionResult.failure(
                error_code=str(metadata.get("error_code", "ACTION_FAILED")),
                content=result.error or result.text or f"{plan.executor} action failed",
                classification_source=str(
                    metadata.get(
                        "classification_source",
                        ClassificationSource.STRUCTURED.value,
                    )
                ),
                retryable=metadata.get("retryable"),
            )
        return replace(result, action_result=action_result)

    @staticmethod
    def _verify_completion(task: Task, workspace=None) -> str:
        """Verify file mutations before allowing a task to be reported done."""
        try:
            if workspace is None:
                if task.verb in {
                    Verb.WRITE,
                    Verb.MODIFY,
                    Verb.DELETE,
                    Verb.COPY,
                    Verb.MOVE,
                }:
                    return (
                        "FILE_OPERATION_UNVERIFIED: 当前执行没有绑定 RunContext.workspace"
                    )
                return ""

            def resolve(value: str):
                return workspace.resolve_path(value)

            if task.verb in (Verb.WRITE, Verb.MODIFY):
                if task.target_type != "file" or not task.target.strip():
                    return ""
                full = resolve(task.target)
                if not full.is_relative_to(workspace.root.resolve()):
                    return f"FILE_WRITE_UNVERIFIED: 文件写入未验证：目标超出 workspace 范围: {task.target}"
                if not full.exists() or not full.is_file():
                    return f"FILE_WRITE_UNVERIFIED: 文件写入未验证：文件不存在: {task.target}"
                if full.stat().st_size == 0:
                    return f"FILE_WRITE_UNVERIFIED: 文件写入未验证：文件为空: {task.target}"
                return ""

            inputs = task.inputs or {}
            if task.verb is Verb.DELETE:
                full = resolve(task.target)
                if not full.is_relative_to(workspace.root.resolve()):
                    return f"FILE_OPERATION_UNVERIFIED: 目标超出 workspace 范围: {task.target}"
                if full.exists():
                    return f"FILE_OPERATION_UNVERIFIED: 删除目标仍存在: {task.target}"
                return ""

            if task.verb in (Verb.COPY, Verb.MOVE):
                source_value = str(inputs.get("source", ""))
                destination_value = str(inputs.get("destination", task.target))
                source = resolve(source_value)
                destination = resolve(destination_value)
                if (
                    not source.is_relative_to(workspace.root.resolve())
                    or not destination.is_relative_to(workspace.root.resolve())
                ):
                    return "FILE_OPERATION_UNVERIFIED: 文件操作目标超出 workspace 范围"
                if not destination.exists() or not destination.is_file():
                    return f"FILE_OPERATION_UNVERIFIED: 目标文件不存在: {destination_value}"
                if task.verb is Verb.COPY:
                    if not source.exists() or source.read_bytes() != destination.read_bytes():
                        return "FILE_OPERATION_UNVERIFIED: 复制后源/目标内容不一致"
                elif source.exists():
                    return f"FILE_OPERATION_UNVERIFIED: 移动源文件仍存在: {source_value}"
                return ""

            return ""
        except Exception as exc:
            return f"FILE_OPERATION_UNVERIFIED: {task.target} ({exc})"

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
        task_dict["failed_component"] = "" if result.success else str(
            metadata.get("failed_component", "")
        )
        task_dict["retryable"] = bool(metadata.get("retryable", False))
        task_dict["observations"].append({
            "action": f"{plan.executor}_executor",
            "tool": metadata.get("executor", plan.executor),
            "tools": list(metadata.get("tools_called", []) or []),
            "status": "succeeded" if result.success else "failed",
            "summary": summary,
            "artifact_ids": [a.id for a in result.artifacts],
            "time_s": round(float(metadata.get("time_s", 0) or 0), 2),
        })

        variables = metadata.get("variables")
        if variables:
            task_dict.setdefault("facts", {}).update(variables)
