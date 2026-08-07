"""Runtime adapter used by the C-4 CLI and Service smoke path.

The public AgentService remains unaware of UniversalAgent.  This module is
the explicit adapter at the boundary where the existing Runtime is launched;
Run state and its durable event are committed before/after the execution in
the SQLite transaction owned by ``DurableRuntimeStoreView``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from agent.runtime import UniversalAgent

from .contracts import ResumeRunRequest, StartRunRequest


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class RuntimeExecutionLauncher:
    """Launch the established UniversalAgent behind the Service Protocol."""

    def __init__(
        self,
        *,
        runtime_factory: Callable[..., Any] = UniversalAgent,
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

        runtime = self._runtime_factory(
            request.user_id,
            tenant_id=request.tenant_id,
            session_context=session_context,
            run_context=run_context,
        )
        try:
            await runtime.run(request.request_text)
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
                    payload=(
                        {"request_id": request.request_id}
                        if not failure_code
                        else self._failure_payload(
                            runtime,
                            failure_code,
                            request.request_id,
                        )
                    ),
                )
            finally:
                self._close_runtime(runtime)

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
        )
        if successful:
            return "COMPLETED", "run_completed", ""

        failure_code = str(
            evidence.get("failure_code", "RUNTIME_EXECUTION_INCOMPLETE")
        )
        if status == "BLOCKED" or failure_code.endswith("UNAVAILABLE"):
            return "BLOCKED", "run_blocked", failure_code
        return "FAILED_TERMINAL", "run_failed", failure_code

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
