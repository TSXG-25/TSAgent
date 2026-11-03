"""ExecutionStage — 单动作执行编排。

唯一执行链：Task → ExecutionPlan → ExecutorFactory → ExecutionResult。
供 AgentLoop 调用：每个模型 tool_call 降成单步 ExecutionPlan 后走这里，
保证证据记录（verified_files / record_*_evidence / inbox）只有一条路径。
"""
import time
from dataclasses import replace
from typing import Any

from agent.state import AgentState
from agent.executor.contract import executor_factory
from agent.workflow import ExecutionContext
from agent.task import Task, ExecutionPlan, Verb
from agent.execution_errors import (
    classify_execution_error,
    stable_error_message,
)
from agent.failure import (
    ClassificationSource,
    failure_fact,
)
from agent.effect_truth import record_effect_result, record_execution_evidence
from agent.inbox import AgentInbox
from agent.action_result import ActionResult
from agent.runtime_gates import has_fresh_evidence
from agent.interruption import (
    CancellationSafetyClass,
    SafeCancellationBoundary,
)


_FILE_MUTATION_VERBS = frozenset({
    Verb.WRITE.value,
    Verb.MODIFY.value,
    Verb.DELETE.value,
    Verb.MOVE.value,
    Verb.COPY.value,
})


class ExecutionStage:
    """单动作执行器。

    持有对 ExecutionOrchestrator 容器的反向引用（访问 _timings / run_context）。
    """

    def __init__(self, orchestrator):
        self._orch = orchestrator

    def _emit_progress(self, event_type: str, payload: dict[str, Any]) -> None:
        """Publish a scoped execution progress fact when a Run owns a bus."""

        run_context = getattr(self._orch, "run_context", None)
        event_bus = getattr(run_context, "event_bus", None)
        if event_bus is not None:
            event_bus.emit(event_type, payload)

    async def execute_action(
        self,
        state: AgentState,
        task_dict: dict,
        plan: ExecutionPlan,
        *,
        user_input: str = "",
    ):
        """执行一个已授权的单步 tool action 并把证据写回 state。

        task_dict 必须已经追加到 ``state["plan"]``。授权检查由调用方
        （AgentLoop）在进入本函数前完成。
        """
        t_exec = time.perf_counter()
        run_context = getattr(self._orch, "run_context", None)
        ws_service = getattr(run_context, "workspace", None)
        cancellation_view = (
            getattr(run_context, "cancellation_view", None)
            if run_context is not None
            else None
        )
        task_obj = Task.from_dict(task_dict)
        if cancellation_view is not None:
            cancellation_view.raise_if_requested(
                SafeCancellationBoundary.BEFORE_TOOL,
                CancellationSafetyClass.BOUNDARY_ONLY,
            )

        self._emit_progress(
            "task_started",
            {
                "task_id": str(task_dict.get("id", "")),
                "verb": str(task_dict.get("verb", "")),
                "executor": plan.executor,
            },
        )
        context = ExecutionContext(
            task=task_obj,
            user_input=user_input,
            facts=dict(task_dict.get("facts", {}) or {}),
            variables={},
        )
        if ws_service is not None:
            context.set_var("workspace", ws_service)
        if run_context is not None:
            context.set_var("artifact_store", run_context.artifacts)
            context.set_var("event_bus", run_context.event_bus)
            context.set_var("cancellation_view", run_context.cancellation_view)
            context.set_var("repository_indexer", run_context.repository_indexer)
        context.set_var("execution_plan", plan)
        context.set_var("conversation_snapshot", state.get("conversation_snapshot"))
        context.set_var("conversation_reference_type", state.get("conversation_reference_type"))
        context.set_var("conversation", state.get("conversation", []))

        try:
            executor = executor_factory.get("tool")
            exec_result = await executor.execute(task_obj, context)
        except Exception as exc:
            exec_result = self._failed_result(plan, exc)

        exec_result = self._ensure_action_result(exec_result, plan)
        if (
            str(task_dict.get("verb", "")).lower() in _FILE_MUTATION_VERBS
            and exec_result.success
            and exec_result.action_result is not None
            and exec_result.action_result.verified is not True
        ):
            error = "UNVERIFIED_EFFECT: 执行器未提供文件变更的核验结果"
            metadata = dict(exec_result.metadata or {})
            metadata.update({
                "error_code": "UNVERIFIED_EFFECT",
                "failed_component": "execution_verifier",
                "retryable": False,
            })
            exec_result = replace(
                exec_result,
                success=False,
                error=error,
                action_result=ActionResult.failure(
                    error_code="UNVERIFIED_EFFECT",
                    content=error,
                    verified=False,
                    retryable=False,
                    classification_source=ClassificationSource.STRUCTURED.value,
                ),
                metadata=metadata,
            )

        completed_action_result = getattr(exec_result, "action_result", None)
        self._emit_progress(
            "task_completed",
            {
                "task_id": str(task_dict.get("id", "")),
                "verb": str(task_dict.get("verb", "")),
                "executor": plan.executor,
                "status": "succeeded" if exec_result.success else "failed",
                "verified": bool(
                    completed_action_result is not None
                    and completed_action_result.verified is True
                ),
                "error_code": str(
                    (exec_result.metadata or {}).get("error_code", "")
                ),
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
            state["runtime_failure_side_effect_state"] = (
                action_result.side_effect_state
                or metadata.get("side_effect_state")
                or "UNKNOWN"
            )

        self._apply_result(task_dict, plan, exec_result)
        record_effect_result(state, task_dict, plan, exec_result)
        record_execution_evidence(state, plan, exec_result)
        inbox = AgentInbox.from_dict(state.get("inbox"))
        step = {
            "task_id": str(task_dict.get("id", "")),
            "tool": plan.steps[0].tool if plan.steps else plan.executor,
            "action": {
                "kind": "tool_call",
                "executor": plan.executor,
                "task_id": str(task_dict.get("id", "")),
                "reason": str(
                    task_dict.get("goal", "") or task_dict.get("description", "")
                ),
            },
        }
        action_result = getattr(exec_result, "action_result", None)
        if action_result is not None:
            step["result"] = action_result.to_dict()
        else:
            step["result"] = {
                "ok": bool(exec_result.success),
                "content": exec_result.text or exec_result.error,
                "error_code": str((exec_result.metadata or {}).get("error_code", "")),
                "verified": bool(exec_result.success),
            }
        inbox.add_step(step)
        state["inbox"] = inbox.to_dict()
        if action_result is not None:
            state["last_action_result"] = action_result.to_dict()
        state["fresh_evidence"] = has_fresh_evidence(state)

        if cancellation_view is not None:
            cancellation_view.raise_if_requested(
                SafeCancellationBoundary.AFTER_TOOL,
                CancellationSafetyClass.BOUNDARY_ONLY,
            )
        self._orch._timings["executor"] = round(
            float(self._orch._timings.get("executor", 0.0))
            + (time.perf_counter() - t_exec),
            3,
        )
        return exec_result

    @staticmethod
    def _failed_result(plan: ExecutionPlan, exc: Exception):
        """将路由/执行异常收敛为统一 ExecutionResult。"""
        from agent.workflow import ExecutionResult

        error_code = classify_execution_error(exc)
        normalized_code = error_code or "ACTION_EXECUTION_FAILED"
        error_message = stable_error_message(exc, fallback="executor failed")
        failure = failure_fact(
            normalized_code,
            message=error_message,
            component=f"{plan.executor}_executor",
            classification_source=ClassificationSource.LEGACY_FALLBACK,
        )
        return ExecutionResult(
            success=False,
            error=f"{plan.executor} executor failed: {error_message}",
            action_result=ActionResult.failure(
                error_code=normalized_code,
                content=error_message,
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
            if verified is None and metadata.get("verifier") not in {None, "", "none"}:
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
    def _apply_result(task_dict: dict, plan: ExecutionPlan, result) -> None:
        """把统一 ExecutionResult 投影回 AgentState 的 Runtime Cache。"""
        task_dict.setdefault("observations", [])
        metadata = result.metadata or {}
        task_dict["verified_files"] = [
            item for item in task_dict.get("verified_files", [])
            if item["path"] not in metadata.get("consumed_files", [])
        ] + list(metadata.get("verified_files", []))
        task_dict.setdefault("consumed_files", []).extend(metadata.get("consumed_files", []))
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
        tools = list(metadata.get("tools_called", []) or [])
        task_dict["observations"].append({
            "action": f"{plan.executor}_executor",
            "tool": metadata.get("executor", plan.executor),
            "tools": tools,
            "tool_used": str(tools[0]) if tools else "",
            "sources": list(metadata.get("sources") or []),
            "status": "succeeded" if result.success else "failed",
            "summary": summary,
            "artifact_ids": [a.id for a in result.artifacts],
            "time_s": round(float(metadata.get("time_s", 0) or 0), 2),
        })

        variables = metadata.get("variables")
        if variables:
            task_dict.setdefault("facts", {}).update(variables)
