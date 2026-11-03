"""Runtime adapter used by the C-4 CLI and Service smoke path.

The public AgentService remains unaware of UniversalAgent.  This module is
the explicit adapter at the boundary where the existing Runtime is launched;
Run state and its durable event are committed before/after the execution in
the SQLite transaction owned by ``DurableRuntimeStoreView``.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from collections.abc import Mapping
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Callable

from .contracts import ResumeRunRequest, StartRunRequest

if TYPE_CHECKING:
    from agent.runtime_store import ArtifactCommitFact


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class RuntimeExecutionLauncher:
    """Launch the established UniversalAgent behind the Service Protocol."""

    _PROGRESS_EVENT_TYPES = (
        "stage_started",
        "stage_completed",
        "task_started",
        "task_completed",
        "assistant_delta",
    )

    def __init__(
        self,
        *,
        runtime_factory: Callable[..., Any] | None = None,
    ) -> None:
        self._runtime_factory = runtime_factory

    async def start(
        self,
        *,
        session_context: Any,
        run_context: Any,
        request: StartRunRequest,
    ) -> None:
        await self._execute(
            session_context=session_context,
            run_context=run_context,
            request=request,
            resumed=False,
        )

    async def resume(
        self,
        *,
        run_context: Any,
        request: ResumeRunRequest,
    ) -> None:
        await self._resume_checkpoint(run_context=run_context, request=request)

    async def _resume_checkpoint(
        self,
        *,
        run_context: Any,
        request: ResumeRunRequest,
    ) -> None:
        durable_view = run_context.durable_store_view
        if durable_view is None:
            raise RuntimeError("Run checkpoint resume requires a durable Run view")

        durable_view.transition_run_with_event(
            run_status="RUNNING",
            event_id=f"run-resumed:{run_context.run_id}:{request.request_id}",
            event_type="run_resumed",
            timestamp=_timestamp(),
            payload={"request_id": request.request_id},
        )
        index = durable_view.get_run_index()
        if index is None:
            evidence = self._resume_failure_evidence(
                "RUN_RESUME_INDEX_MISSING",
                "此 Run 没有可恢复的持久化 Workflow checkpoint；未重新执行原请求。",
                status="BLOCKED",
            )
            self._commit_resume_terminal(
                run_context,
                request,
                evidence,
                runtime_answer=evidence["answer"],
            )
            return

        from agent.bootstrap import load_all_workflows
        from agent.checkpoint import (
            ExternalStateGuard,
            NoEffectConfirmation as CheckpointNoEffectConfirmation,
            ResumeAction,
        )
        from agent.registry.workflow_registry import workflow_registry
        from agent.run_resume import (
            RunResumeCoordinator,
            RunResumeReasonCode,
            RunResumeRequest,
        )
        from agent.run_resume.execution_context import workflow_execution_context

        load_all_workflows()
        workflows = {
            workflow_id: workflow_registry.get(workflow_id)
            for workflow_id in index.workflow_sequence
        }
        if any(workflow is None for workflow in workflows.values()):
            evidence = self._resume_failure_evidence(
                "RUN_RESUME_WORKFLOW_MISSING",
                "Run index 引用的 Workflow 定义当前不可用；未执行任何 Stage。",
                status="BLOCKED",
            )
            self._commit_resume_terminal(
                run_context,
                request,
                evidence,
                runtime_answer=evidence["answer"],
            )
            return

        active_workflow_id = index.active_workflow_id
        versions = tuple(
            (workflow_id, workflow.version)
            for workflow_id, workflow in workflows.items()
            if workflow is not None
        )
        resume_request = RunResumeRequest(
            requested_run_id=request.run_id,
            candidate_run_ids=(request.run_id,),
            requested_workflow_id=active_workflow_id,
            requested_checkpoint_id=request.checkpoint_id or "",
            requested_action=request.action or ResumeAction.RESUME_EXACT,
            current_workflow_versions=versions,
            observed_artifacts=index.artifacts,
            external_state_evidence=tuple(
                ExternalStateGuard.from_dict(item.to_dict())
                for item in request.external_state_evidence
            ),
            no_effect_confirmation=(
                CheckpointNoEffectConfirmation.from_dict(
                    request.no_effect_confirmation.to_dict()
                )
                if request.no_effect_confirmation is not None
                else None
            ),
            rehydrated_from_store=True,
        )
        coordinator = RunResumeCoordinator(
            runtime_store_view=durable_view,
            workflows=workflows,
        )
        watchdog = self._start_timeout_watchdog(run_context, request)
        subscriptions = self._bind_progress_events(
            run_context=run_context,
            request=request,
        )
        try:
            execution = await coordinator.execute_or_resume(
                request.run_id,
                lambda selected: workflow_execution_context(
                    selected,
                    index.workflow(selected.id),
                    run_context=run_context,
                ),
                attempt_id=request.request_id,
                request=resume_request,
            )
            durable_view.store.append_event(
                durable_view.tenant_id,
                durable_view.session_id,
                durable_view.run_id,
                event_id=f"resume-decided:{run_context.run_id}:{request.request_id}",
                event_type="resume_decided",
                timestamp=_timestamp(),
                payload=execution.decision.to_dict(),
                run_revision=durable_view.head().current_revision,
                expected_store_generation=durable_view.store_generation,
            )
            result = execution.execution_result
            summary = (
                str(result.outputs.get("_summary", "") or "")
                if result is not None
                else ""
            )
            metadata = result.metadata or {} if result is not None else {}
            terminal_verified = bool(metadata.get("terminal_outputs_verified", False))
            unresolved = tuple(metadata.get("unresolved_resume_diagnostics", ()) or ())
            allowed = execution.decision.disposition.value == "ALLOW"
            requires_clarification = (
                execution.decision.disposition.value == "REQUIRE_CLARIFICATION"
            )
            already_completed = (
                execution.decision.reason_code is RunResumeReasonCode.RUN_COMPLETED
            )
            output_workflow_id = (
                active_workflow_id
                or (
                    execution.index.completed_workflow_ids[-1]
                    if execution.index.completed_workflow_ids
                    else ""
                )
            )
            if already_completed:
                completed_checkpoint = (
                    durable_view.latest_checkpoint(
                        workflow_id=output_workflow_id
                    )
                    if output_workflow_id
                    else None
                )
                if (
                    completed_checkpoint is not None
                    and completed_checkpoint.status.value == "COMPLETED"
                    and completed_checkpoint.verifier_status == "VERIFIED"
                    and completed_checkpoint.target_summary.strip()
                ):
                    summary = completed_checkpoint.target_summary
                    terminal_verified = True
            successful = bool(
                terminal_verified
                and not unresolved
                and summary.strip()
                and (
                    already_completed
                    or (
                        allowed
                        and result is not None
                        and result.success
                    )
                )
            )
            if successful:
                evidence = {
                    "terminal_status": "COMPLETED",
                    "terminal_outputs_verified": True,
                    "budget_exhausted": False,
                    "runtime_pending": False,
                    "task_failures": [],
                    "failure_code": "",
                    "answer": summary,
                    "answer_required": True,
                    "user_visible_output_verified": True,
                    "freshness_required": False,
                    "source_grounding_required": False,
                    "fresh_evidence": True,
                    "resolved_request": (
                        index.workflow(output_workflow_id).target_summary
                        if output_workflow_id and index.workflow(output_workflow_id)
                        else ""
                    ),
                    "artifact_ids": [
                        artifact.artifact_id
                        for artifact in execution.index.artifacts
                        if artifact.exists and artifact.verified
                    ],
                }
            else:
                failure_code = (
                    f"RUN_RESUME_{execution.decision.reason_code.value}"
                    if not allowed
                    else "WORKFLOW_RESUME_OUTPUTS_UNVERIFIED"
                    if not terminal_verified or unresolved or not summary.strip()
                    else "WORKFLOW_EXECUTION_INCOMPLETE"
                )
                detail = (
                    execution.decision.clarification_question
                    if requires_clarification
                    else execution.decision.reason_code.value
                    if result is None
                    else result.error or "Workflow resume did not verify terminal outputs"
                )
                evidence = self._resume_failure_evidence(
                    failure_code,
                    str(detail),
                    status=(
                        "WAITING_USER"
                        if requires_clarification
                        else "BLOCKED"
                        if not allowed
                        else "FAILED_TERMINAL"
                    ),
                )
                if requires_clarification:
                    evidence["clarification_question"] = str(detail)
                    evidence["answer"] = str(detail)
                evidence["resolved_request"] = (
                    index.workflow(output_workflow_id).target_summary
                    if output_workflow_id and index.workflow(output_workflow_id)
                    else ""
                )
            if self._converge_interruption(run_context, SimpleNamespace(
                last_run_evidence=evidence,
            )):
                return
            self._commit_resume_terminal(
                run_context,
                request,
                evidence,
                runtime_answer=str(evidence.get("answer", "")),
                artifact_ids=tuple(evidence.get("artifact_ids", ())),
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            evidence = self._resume_failure_evidence(
                "RUN_RESUME_EXECUTION_FAILED",
                str(error),
                status="FAILED_TERMINAL",
            )
            self._commit_resume_terminal(
                run_context,
                request,
                evidence,
                runtime_answer=evidence["answer"],
            )
        finally:
            for subscription in subscriptions:
                subscription.close()
            await self._stop_timeout_watchdog(watchdog)

    @staticmethod
    def _resume_failure_evidence(
        failure_code: str,
        detail: str,
        *,
        status: str,
    ) -> dict[str, Any]:
        return {
            "terminal_status": status,
            "terminal_outputs_verified": False,
            "budget_exhausted": False,
            "runtime_pending": False,
            "task_failures": [{"error_code": failure_code, "error": detail}],
            "failure_code": failure_code,
            "failure_detail": detail[:300],
            "failure_class": "resume",
            "failed_component": "run_resume_coordinator",
            "answer": detail,
            "answer_required": True,
            "user_visible_output_verified": True,
            "freshness_required": False,
            "source_grounding_required": False,
            "fresh_evidence": True,
        }

    def _commit_resume_terminal(
        self,
        run_context: Any,
        request: ResumeRunRequest,
        evidence: dict[str, Any],
        *,
        runtime_answer: str,
        artifact_ids: tuple[str, ...] = (),
    ) -> None:
        durable_view = run_context.durable_store_view
        terminal_runtime = SimpleNamespace(last_run_evidence=evidence)
        run_status, event_type, failure_code = self._terminal_outcome(terminal_runtime)
        payload: dict[str, Any] = {
            "continuation": {
                "request": "",
                "goal": evidence.get("resolved_request", ""),
                "missing": [] if not failure_code else [failure_code],
                "observations": [],
                "acceptance_evidence": [],
            },
        }
        if run_status == "WAITING_USER":
            payload.update({
                "request_id": request.request_id,
                "failure_code": failure_code,
                "clarification_question": evidence.get("clarification_question", ""),
            })
        elif not failure_code:
            payload["request_id"] = request.request_id
        else:
            payload.update(
                self._failure_payload(terminal_runtime, failure_code, request.request_id)
            )
        durable_view.transition_run_with_event(
            run_status=run_status,
            event_id=f"run-{event_type.removeprefix('run_')}:{run_context.run_id}:{request.request_id}",
            event_type=event_type,
            timestamp=_timestamp(),
            payload=payload,
            run_output=self._run_output_payload(
                terminal_runtime,
                runtime_answer,
                artifact_ids=artifact_ids,
            ),
            artifacts=(),
        )

    async def _execute(
        self,
        *,
        session_context: Any,
        run_context: Any,
        request: StartRunRequest | ResumeRunRequest,
        resumed: bool,
    ) -> None:
        durable_view = run_context.durable_store_view
        if durable_view is None:
            raise RuntimeError("RuntimeExecutionLauncher requires a durable Run view")

        event_prefix = "resume" if resumed else "start"
        durable_view.transition_run_with_event(
            run_status="RUNNING",
            event_id=f"run-{event_prefix}ed:{run_context.run_id}:{request.request_id}",
            event_type="run_resumed" if resumed else "run_started",
            timestamp=_timestamp(),
            payload={"request_id": request.request_id},
            expected_status="CREATED" if not resumed else None,
        )

        # UniversalAgent construction imports the cognition/orchestrator
        # graph and may take several seconds on a cold process.  It is a
        # synchronous constructor, so running it on the event-loop thread
        # delays the JSONL response even though the Service already accepted
        # the durable Run.  Keep the actual Runtime coroutine on the event
        # loop, but move only this blocking construction boundary to a worker.
        runtime = await asyncio.to_thread(
            self._build_runtime,
            request,
            session_context,
            run_context,
        )
        watchdog = self._start_timeout_watchdog(run_context, request)
        progress_subscriptions = self._bind_progress_events(
            run_context=run_context,
            request=request,
        )
        try:
            runtime_answer = ""
            try:
                runtime_answer = str(await runtime.run(request.request_text) or "")
            except BaseException:
                try:
                    durable_view.transition_run_with_event(
                        run_status="FAILED_TERMINAL",
                        event_id=f"run-failed:{run_context.run_id}:{request.request_id}",
                        event_type="run_failed",
                        timestamp=_timestamp(),
                        payload={
                            **self._failure_payload(None, "RUNTIME_EXCEPTION", request.request_id),
                            "continuation": {
                                "request": request.request_text,
                                "goal": request.request_text,
                                "missing": ["RUNTIME_EXCEPTION"],
                                "observations": [],
                            },
                        },
                    )
                finally:
                    self._close_runtime(runtime)
                raise
            else:
                try:
                    if self._converge_interruption(run_context, runtime):
                        return
                    artifact_facts = self._artifact_commit_facts(runtime)
                    runtime_evidence = getattr(runtime, "last_run_evidence", None) or {}
                    expected_files = {item['reference'] for item in runtime_evidence.get('verified_artifacts', [])}
                    unavailable = expected_files - {item.reference for item in artifact_facts}
                    if unavailable:
                        failure_code = runtime_evidence.get('failure_code') or 'ARTIFACT_CHANGED_AFTER_VERIFICATION'
                        preserved = '、'.join(item.reference for item in artifact_facts)
                        runtime_evidence.update(
                            terminal_status='FAILED_TERMINAL', terminal_outputs_verified=False,
                            failure_code=failure_code,
                            answer=f"任务未完成（{failure_code}）。终态发布前文件证据失效：{'、'.join(sorted(unavailable))}。"
                                   + (f' 已核验保留的文件：{preserved}。' if preserved else ''),
                        )
                    run_status, event_type, failure_code = self._terminal_outcome(runtime)
                    event_id = (
                        f"run-completed:{run_context.run_id}:{request.request_id}"
                        if event_type == "run_completed"
                        else (
                            f"run-blocked:{run_context.run_id}:{request.request_id}"
                            if event_type == "run_blocked"
                            else f"run-failed:{run_context.run_id}:{request.request_id}"
                        )
                    )
                    durable_view.transition_run_with_event(
                        run_status=run_status,
                        event_id=event_id,
                        event_type=event_type,
                        timestamp=_timestamp(),
                        payload={
                            "continuation": {
                                "request": request.request_text if isinstance(request, StartRunRequest) else "",
                                "goal": runtime_evidence.get("resolved_request", ""),
                                "goal_contract": runtime_evidence.get("goal_contract", {}),
                                "missing": runtime_evidence.get("goal_missing", []),
                                "observations": runtime_evidence.get("action_observations", []),
                                "acceptance_evidence": runtime_evidence.get("acceptance_evidence", []),
                            },
                            **(
                            {"request_id": request.request_id}
                            if not failure_code
                            else self._failure_payload(
                                runtime,
                                failure_code,
                                request.request_id,
                            )
                            ),
                        },
                        run_output=self._run_output_payload(
                            runtime,
                            runtime_answer,
                            artifact_ids=tuple(
                                artifact.artifact_id for artifact in artifact_facts
                            ),
                        ),
                        artifacts=artifact_facts,
                    )
                finally:
                    self._close_runtime(runtime)
        finally:
            for subscription in progress_subscriptions:
                subscription.close()
            await self._stop_timeout_watchdog(watchdog)

    def _bind_progress_events(
        self,
        *,
        run_context: Any,
        request: StartRunRequest | ResumeRunRequest,
    ) -> tuple[Any, ...]:
        """Persist scoped Runtime progress into the Service event stream.

        Runtime owns the in-process EventBus and emits progress facts there.
        The Service adapter is the only bridge to the durable public event
        repository, so CLI/desktop consumers observe the same ordered facts
        without importing Runtime internals.
        """

        event_bus = getattr(run_context, "event_bus", None)
        durable_view = getattr(run_context, "durable_store_view", None)
        if event_bus is None or durable_view is None:
            return ()

        sequence = 0
        subscriptions: list[Any] = []

        def persist(event_type: str, data: object) -> None:
            nonlocal sequence
            sequence += 1
            payload = (
                dict(data)
                if isinstance(data, Mapping)
                else {"detail": str(data)}
            )
            payload["source"] = "runtime"
            workflow_id = str(payload.get("workflow_id", "") or "").strip() or None
            stage_id = str(payload.get("stage_id", "") or "").strip() or None
            task_id = str(payload.get("task_id", "") or "").strip() or None
            durable_view.store.append_event(
                durable_view.tenant_id,
                durable_view.session_id,
                durable_view.run_id,
                event_id=(
                    f"runtime-progress:{durable_view.run_id}:"
                    f"{request.request_id}:{sequence}"
                ),
                event_type=event_type,
                timestamp=_timestamp(),
                payload=payload,
                workflow_id=workflow_id,
                stage_id=stage_id,
                task_id=task_id,
                run_revision=durable_view.head().current_revision,
                expected_store_generation=durable_view.store_generation,
            )

        for event_type in self._PROGRESS_EVENT_TYPES:
            subscriptions.append(
                event_bus.subscribe(
                    event_type,
                    lambda data, event_type=event_type: persist(event_type, data),
                )
            )
        return tuple(subscriptions)

    def _build_runtime(
        self,
        request: StartRunRequest | ResumeRunRequest,
        session_context: Any,
        run_context: Any,
    ) -> Any:
        runtime_factory = self._runtime_factory
        if runtime_factory is None:
            from agent.runtime import UniversalAgent

            runtime_factory = UniversalAgent
        return runtime_factory(
            request.user_id,
            tenant_id=request.tenant_id,
            session_context=session_context,
            run_context=run_context,
        )

    @staticmethod
    def _run_timeout_seconds(request: StartRunRequest | ResumeRunRequest) -> float:
        metadata = getattr(request, "metadata", {}) or {}
        raw = metadata.get("run_timeout_seconds")
        if raw is None:
            raw = os.getenv("TSAGENT_RUN_TIMEOUT_SECONDS", "0")
        try:
            seconds = float(raw)
        except (TypeError, ValueError):
            raise ValueError("run_timeout_seconds must be a finite positive number")
        if seconds < 0 or seconds == float("inf") or seconds != seconds:
            raise ValueError("run_timeout_seconds must be a finite positive number")
        return seconds

    def _start_timeout_watchdog(
        self,
        run_context: Any,
        request: StartRunRequest | ResumeRunRequest,
    ) -> asyncio.Task[None] | None:
        seconds = self._run_timeout_seconds(request)
        if seconds == 0:
            return None
        return asyncio.create_task(
            self._run_timeout_watchdog(run_context, request, seconds),
            name=f"tsagent-run-timeout:{run_context.run_id}",
        )

    @staticmethod
    async def _run_timeout_watchdog(
        run_context: Any,
        request: StartRunRequest | ResumeRunRequest,
        seconds: float,
    ) -> None:
        await asyncio.sleep(seconds)
        durable_view = run_context.durable_store_view
        if durable_view is None:
            return
        from agent.interruption import CancellationCoordinator

        CancellationCoordinator(durable_view.store).request_run_timeout(
            tenant_id=run_context.tenant_id,
            user_id=run_context.user_id,
            session_id=run_context.session_id,
            run_id=run_context.run_id,
            request_id=f"timeout:{run_context.run_id}:{request.request_id}",
            requested_by="runtime-watchdog",
        )

    @staticmethod
    async def _stop_timeout_watchdog(
        watchdog: asyncio.Task[None] | None,
    ) -> None:
        if watchdog is None:
            return
        if not watchdog.done():
            watchdog.cancel()
        await asyncio.gather(watchdog, return_exceptions=True)

    @staticmethod
    def _converge_interruption(run_context: Any, runtime: Any) -> bool:
        """Finalize a durable intent only after Runtime reached a safe boundary."""

        durable_view = run_context.durable_store_view
        cancellation_view = getattr(run_context, "cancellation_view", None)
        if durable_view is None or cancellation_view is None:
            return False
        record = cancellation_view.current()
        if record is None:
            return False
        evidence = getattr(runtime, "last_run_evidence", None) or {}
        if not bool(evidence.get("interruption_requested", False)):
            # Close the race between Runtime's last safe-point check and the
            # Service terminal transition.  A durable intent always wins over
            # a stale successful coroutine return.
            evidence["interruption_requested"] = True
        artifacts = RuntimeExecutionLauncher._artifact_commit_facts(runtime)
        artifact_ids = tuple(item.artifact_id for item in artifacts)
        evidence["artifact_ids"] = list(artifact_ids)
        terminal_payload = {
            "partial_execution": {
                "completed_actions": list(evidence.get("completed_actions", [])),
                "verified_artifacts": [
                    {
                        "artifact_id": item.artifact_id,
                        "reference": item.reference,
                        "digest": item.digest,
                    }
                    for item in artifacts
                ],
                "interrupted_action": evidence.get("interrupted_action"),
            },
        }
        run_output = RuntimeExecutionLauncher._run_output_payload(
            runtime,
            str(evidence.get("answer", "") or ""),
            artifact_ids=artifact_ids,
        )
        from agent.interruption import CancellationCoordinator

        CancellationCoordinator(durable_view.store).mark_safe_to_interrupt(
            tenant_id=run_context.tenant_id,
            session_id=run_context.session_id,
            run_id=run_context.run_id,
            request_id=record.intent.request_id,
            writer_id=durable_view.writer_id,
            fence_token=durable_view.fence_epoch,
            terminal_payload=terminal_payload,
            run_output=run_output,
            artifacts=artifacts,
        )
        return True

    @staticmethod
    def _terminal_outcome(runtime: Any) -> tuple[str, str, str]:
        """Use Runtime evidence, not coroutine return, to commit terminal state."""
        evidence = getattr(runtime, "last_run_evidence", None)
        # Small legacy/fake runtimes used by adapter tests have no evidence;
        # UniversalAgent always publishes it, so this compatibility path is
        # not used by the production Runtime.
        if evidence is None:
            return "COMPLETED", "run_completed", ""

        status = str(evidence.get("terminal_status", "") or "")
        successful = (
            status == "COMPLETED"
            and bool(evidence.get("terminal_outputs_verified", False))
            and not bool(evidence.get("budget_exhausted", False))
            and not bool(evidence.get("runtime_pending", False))
            and not evidence.get("task_failures")
            and (
                not bool(evidence.get("answer_required", False))
                or bool(evidence.get("user_visible_output_verified", False))
            )
            and (
                not bool(
                    evidence.get("freshness_required", False)
                    or evidence.get("source_grounding_required", False)
                )
                or bool(evidence.get("fresh_evidence", False))
            )
        )
        if successful:
            return "COMPLETED", "run_completed", ""

        failure_code = str(
            evidence.get("failure_code", "RUNTIME_EXECUTION_INCOMPLETE")
        )
        if status == "WAITING_USER":
            return "WAITING_USER", "run_waiting_user", failure_code

        if (
            evidence.get("answer_required", False)
            and not evidence.get("user_visible_output_verified", False)
            and status == "COMPLETED"
        ):
            failure_code = "MISSING_USER_OUTPUT"
        if (
            evidence.get("freshness_required", False)
            or evidence.get("source_grounding_required", False)
        ) and not evidence.get("fresh_evidence", False) and status == "COMPLETED":
            failure_code = "RESEARCH_TOOL_UNAVAILABLE"
        if status == "BLOCKED" or failure_code in {
            "RESEARCH_TOOL_UNAVAILABLE",
            "UNSUPPORTED_CAPABILITY",
        }:
            return "BLOCKED", "run_blocked", failure_code
        return "FAILED_TERMINAL", "run_failed", failure_code

    @staticmethod
    def _run_output_payload(
        runtime: Any,
        runtime_answer: str,
        *,
        artifact_ids: tuple[str, ...] = (),
    ) -> dict[str, Any] | None:
        """Project only a non-empty user-visible answer into durable storage."""

        raw_evidence = getattr(runtime, "last_run_evidence", None)
        # Once Runtime evidence exists, its answer field is authoritative. A
        # coroutine return from a failed/budget-exhausted runtime is not an
        # independent success fact and must not become durable output.
        evidence = raw_evidence if isinstance(raw_evidence, dict) else None
        text = str(
            (evidence.get("answer", "") if evidence is not None else runtime_answer)
            or ""
        )
        if not text.strip():
            return None
        evidence_ids = evidence.get("evidence_ids", ()) if evidence is not None else ()
        existing_artifact_ids = (
            evidence.get("artifact_ids", ()) if evidence is not None else ()
        )
        if isinstance(existing_artifact_ids, str) or not isinstance(
            existing_artifact_ids,
            (list, tuple, set),
        ):
            existing_artifact_ids = ()
        all_artifact_ids: list[str] = []
        for artifact_id in (*existing_artifact_ids, *artifact_ids):
            value = str(artifact_id or "").strip()
            if value and value not in all_artifact_ids:
                all_artifact_ids.append(value)
        return {
            "text": text,
            "evidence_ids": list(evidence_ids) if isinstance(evidence_ids, (list, tuple)) else [],
            "artifact_ids": all_artifact_ids,
        }

    @staticmethod
    def _artifact_commit_facts(runtime: Any) -> tuple[ArtifactCommitFact, ...]:
        from agent.runtime_store import ArtifactCommitFact

        """Turn scoped, verifier-approved files into durable artifact facts."""

        evidence = getattr(runtime, "last_run_evidence", None)
        if not isinstance(evidence, dict):
            return ()
        run_context = getattr(runtime, "run_context", None)
        workspace = getattr(run_context, "workspace", None)
        run_id = str(getattr(run_context, "run_id", "") or "")
        if workspace is None or not run_id:
            return ()

        facts: list[ArtifactCommitFact] = []
        seen: set[str] = set()
        for item in evidence.get("verified_artifacts", ()) or ():
            if not isinstance(item, dict):
                continue
            reference = str(item.get("reference", "") or "").strip()
            if not reference:
                continue
            try:
                resolved = workspace.resolve_path(reference, must_exist=True)
                if resolved.is_dir():
                    continue
                canonical_reference = workspace.relative_path(resolved)
                content_digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
                if content_digest != item['digest']:
                    continue
            except (OSError, ValueError, PermissionError):
                # A task that lost its verified file must not be published as
                # a durable artifact. The task verifier remains authoritative.
                continue
            artifact_id = "artifact-" + hashlib.sha256(
                f"{run_id}:{canonical_reference}".encode("utf-8")
            ).hexdigest()[:24]
            if artifact_id in seen:
                continue
            seen.add(artifact_id)
            producer_task_id = str(item.get("producer_task_id", "") or "")
            producer_stage_id = str(
                item.get("producer_stage_id", "task:unknown") or "task:unknown"
            )
            evidence_digest = hashlib.sha256(
                f"{canonical_reference}:{content_digest}".encode("utf-8")
            ).hexdigest()
            facts.append(
                ArtifactCommitFact(
                    artifact_id=artifact_id,
                    artifact_type=str(item.get("artifact_type", "file") or "file"),
                    reference=canonical_reference,
                    digest=content_digest,
                    producer_workflow_id=str(
                        item.get("producer_workflow_id", "runtime-execution")
                        or "runtime-execution"
                    ),
                    producer_stage_id=producer_stage_id,
                    exists=True,
                    verified=True,
                    verification_evidence_digest=evidence_digest,
                    producer_task_id=producer_task_id,
                )
            )
        return tuple(facts)

    @staticmethod
    def _failure_payload(
        runtime: Any | None,
        failure_code: str,
        request_id: str,
    ) -> dict[str, Any]:
        """Build a stable, secret-free durable failure fact."""
        evidence = getattr(runtime, "last_run_evidence", None) or {}
        failure_class = str(evidence.get("failure_class", "") or "")
        if not failure_class:
            failure_class = "provider" if failure_code.startswith("PROVIDER_") else "execution"
        failed_component = str(evidence.get("failed_component", "") or "runtime")
        diagnostic_event_id = ""
        diagnostics = evidence.get("diagnostics", [])
        if isinstance(diagnostics, list) and diagnostics:
            first = diagnostics[0]
            if isinstance(first, dict):
                diagnostic_event_id = str(first.get("event_id", "") or "")
        return {
            "request_id": request_id,
            "failure_code": str(failure_code),
            **(
                {"failure_detail": str(evidence["failure_detail"])[:300]}
                if str(evidence.get("failure_detail", "") or "").strip()
                else {}
            ),
            "failure_class": failure_class[:80],
            "failed_component": failed_component[:120],
            "retryable": bool(
                evidence.get("retryable", False)
                or failure_code in {
                    "PROVIDER_TIMEOUT",
                    "PROVIDER_NETWORK",
                    "PROVIDER_UNAVAILABLE",
                }
            ),
            **(
                {"diagnostic_event_id": diagnostic_event_id[:120]}
                if diagnostic_event_id
                else {}
            ),
        }

    @staticmethod
    def _close_runtime(runtime: Any) -> None:
        close = getattr(runtime, "close", None)
        if callable(close):
            close()


__all__ = ["RuntimeExecutionLauncher"]
