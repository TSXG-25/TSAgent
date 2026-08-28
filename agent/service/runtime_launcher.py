"""Runtime adapter used by the C-4 CLI and Service smoke path.

The public AgentService remains unaware of UniversalAgent.  This module is
the explicit adapter at the boundary where the existing Runtime is launched;
Run state and its durable event are committed before/after the execution in
the SQLite transaction owned by ``DurableRuntimeStoreView``.
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable

from .contracts import ResumeRunRequest, StartRunRequest

if TYPE_CHECKING:
    from agent.runtime_store import ArtifactCommitFact


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class RuntimeExecutionLauncher:
    """Launch the established UniversalAgent behind the Service Protocol."""

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
        await self._execute(
            session_context=run_context.session,
            run_context=run_context,
            request=request,
            resumed=True,
        )

    async def _execute(
        self,
        *,
        session_context: Any,
        run_context: Any,
        request: StartRunRequest | ResumeRunRequest,
        resumed: bool,
    ) -> None:
        import asyncio

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
                        payload=self._failure_payload(
                            None,
                            "RUNTIME_EXCEPTION",
                            request.request_id,
                        ),
                    )
                finally:
                    self._close_runtime(runtime)
                raise
            else:
                try:
                    if self._converge_interruption(run_context, runtime):
                        return
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
                    artifact_facts = self._artifact_commit_facts(runtime)
                    durable_view.transition_run_with_event(
                        run_status=run_status,
                        event_id=event_id,
                        event_type=event_type,
                        timestamp=_timestamp(),
                        payload=(
                            {"request_id": request.request_id}
                            if not failure_code
                            else self._failure_payload(
                                runtime,
                                failure_code,
                                request.request_id,
                            )
                        ),
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
            await self._stop_timeout_watchdog(watchdog)

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
        import asyncio

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
        import asyncio

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
        import asyncio

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
        from agent.interruption import CancellationCoordinator

        CancellationCoordinator(durable_view.store).mark_safe_to_interrupt(
            tenant_id=run_context.tenant_id,
            session_id=run_context.session_id,
            run_id=run_context.run_id,
            request_id=record.intent.request_id,
            writer_id=durable_view.writer_id,
            fence_token=durable_view.fence_epoch,
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
