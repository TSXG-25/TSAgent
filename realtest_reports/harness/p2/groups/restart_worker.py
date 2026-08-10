"""Child process used by the P2-R1 crash/restart acceptance harness.

The worker uses the real AgentService, SQLite Runtime Store, RunContext,
WorkspaceService, RunResumeCoordinator, WorkflowExecutor, and durable event
path.  Only the Provider is replaced by deterministic workflow inputs.

Crash injection is observational: a hook writes a marker only after the
selected durable milestone is visible, then waits for the parent to send
SIGKILL.  It never creates the checkpoint, effect, event, or Run index used by
the assertion.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:  # pragma: no cover - direct CLI execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from agent.executor.executors.workflow import WorkflowExecutor
from agent.executor.contract import executor_factory
from agent.run_resume import (
    RunResumeCoordinator,
    RunResumeIndex,
    RunWorkflowStatus,
    WorkflowDependency,
    WorkflowSummary,
)
from agent.service import (
    AgentService,
    ResumeRunRequest,
    RunLookupRequest,
    RunStatus,
    ServiceContextFactory,
    StartRunRequest,
)
from agent.runtime_store import SqliteRuntimeStore
from agent.workflow import (
    ExecutionContext,
    ExecutionResult,
    ExecutionSpec,
    ExecutorType,
    OutputArtifact,
    Stage,
    ToolArgument,
    Workflow,
)


CRASH_POINTS = {
    "R01": "after_run_active",
    "R02": "after_effect_commit",
    "R03": "after_checkpoint_commit",
    "R04": "workflow_b_active",
}
TERMINAL = {
    RunStatus.COMPLETED,
    RunStatus.FAILED_TERMINAL,
    RunStatus.BLOCKED,
    RunStatus.CANCELLED,
}


class DeterministicFileExecutor:
    """Network-free executor installed only inside the crash child process."""

    async def execute(self, task: Any, context: ExecutionContext) -> ExecutionResult:
        relative_path = str(task.inputs.get("path", "")).strip()
        content = str(task.inputs.get("content", ""))
        workspace = context.get_var("workspace")
        if not relative_path or workspace is None:
            return ExecutionResult(
                success=False,
                error="deterministic crash worker requires path and scoped workspace",
                metadata={"executor": "deterministic-file"},
            )
        workspace.write_text(relative_path, content)
        target = workspace.resolve_path(relative_path, must_exist=True)
        return ExecutionResult(
            success=True,
            outputs={"text": content},
            metadata={
                "executor": "deterministic-file",
                "external_reference": relative_path,
                "size": target.stat().st_size,
            },
        )


def _install_deterministic_executor() -> None:
    executor_factory.register("tool", DeterministicFileExecutor)
    executor_factory.register("llm", DeterministicFileExecutor)


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    with path.open("wb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def _append_audit(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(descriptor, encoded)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class CrashController:
    def __init__(self, marker: Path) -> None:
        self.point = os.environ.get("TSAGENT_TEST_CRASH_POINT", "").strip()
        self.marker = marker

    def observe(self, point: str, evidence: dict[str, Any]) -> None:
        if self.point != point:
            return
        _write_json(
            self.marker,
            {
                "pid": os.getpid(),
                "point": point,
                "evidence": evidence,
                "observed_at": _timestamp(),
            },
        )
        # The parent waits for the fsync'd marker and then sends SIGKILL.  A
        # blocking loop is deliberate: no graceful close/finally path runs.
        while True:
            time.sleep(60)


def _write_stage(stage_id: str, path: str, content: str, output_type: str) -> Stage:
    return Stage(
        id=stage_id,
        description=f"write deterministic artifact {path}",
        execution=ExecutionSpec(executor=ExecutorType.TOOL, max_retries=0),
        arguments=[
            ToolArgument(param="path", constant=path),
            ToolArgument(param="content", constant=content),
        ],
        outputs=[OutputArtifact(type=output_type)],
        idempotent=True,
    )


def build_workflows(case_id: str) -> dict[str, Workflow]:
    if case_id == "R01":
        workflow = Workflow(
            id="wf-main",
            version="1.0.0",
            description="two-stage resumable workflow",
            stages=[
                _write_stage("r01-stage-1", "output/r01-a.txt", "r01-a\n", "r01_a"),
                _write_stage("r01-stage-2", "output/r01-b.txt", "r01-b\n", "r01_b"),
            ],
        )
        return {workflow.id: workflow}
    if case_id == "R02":
        workflow = Workflow(
            id="wf-effect",
            version="1.0.0",
            description="effect reconciliation workflow",
            stages=[
                _write_stage(
                    "r02-effect-write",
                    "output/effect.txt",
                    "external-effect-once\n",
                    "effect_file",
                )
            ],
        )
        return {workflow.id: workflow}
    if case_id == "R03":
        workflow = Workflow(
            id="wf-event",
            version="1.0.0",
            description="checkpoint response-window workflow",
            stages=[
                _write_stage(
                    "r03-event-write",
                    "output/event.txt",
                    "event-checkpoint\n",
                    "event_file",
                )
            ],
        )
        return {workflow.id: workflow}
    if case_id == "R04":
        workflow_a = Workflow(
            id="wf-a",
            version="1.0.0",
            description="completed upstream workflow A",
            stages=[
                _write_stage("r04-a-write", "output/a.txt", "workflow-a\n", "a_file")
            ],
        )
        workflow_b = Workflow(
            id="wf-b",
            version="1.0.0",
            description="active downstream workflow B",
            stages=[
                _write_stage("r04-b-write", "output/b.txt", "workflow-b\n", "b_file")
            ],
        )
        return {workflow_a.id: workflow_a, workflow_b.id: workflow_b}
    raise ValueError(f"unknown P2-R case: {case_id}")


def build_index(case_id: str, *, run_id: str, session_id: str, user_id: str) -> RunResumeIndex:
    workflows = build_workflows(case_id)
    sequence = tuple(workflows)
    summaries = tuple(
        WorkflowSummary(
            workflow_id=workflow_id,
            workflow_version=workflow.version,
            status=RunWorkflowStatus.PENDING,
            depends_on=((sequence[index - 1],) if index > 0 else ()),
        )
        for index, (workflow_id, workflow) in enumerate(workflows.items())
    )
    dependencies = tuple(
        WorkflowDependency(
            workflow_id,
            ((sequence[index - 1],) if index > 0 else ()),
        )
        for index, workflow_id in enumerate(sequence)
    )
    return RunResumeIndex(
        run_id=run_id,
        workflow_sequence=sequence,
        workflows=summaries,
        completed_workflow_ids=(),
        active_workflow_id="",
        active_checkpoint_id="",
        pending_workflow_ids=sequence,
        workflow_dependencies=dependencies,
        session_id=session_id,
        conversation_id=session_id,
        user_scope=user_id,
        created_at=_timestamp(),
        updated_at=_timestamp(),
    )


class InstrumentedWorkflowExecutor(WorkflowExecutor):
    def __init__(
        self,
        *,
        case_id: str,
        controller: CrashController,
        audit_path: Path,
    ) -> None:
        super().__init__()
        self.case_id = case_id
        self.controller = controller
        self.audit_path = audit_path

    async def execute(
        self,
        workflow: Workflow,
        context: ExecutionContext,
        *,
        checkpoint_request: Any = None,
    ) -> Any:
        if self.case_id == "R04" and workflow.id == "wf-b":
            checkpoint = (
                checkpoint_request.checkpoint
                if checkpoint_request is not None
                else None
            )
            self.controller.observe(
                "workflow_b_active",
                {
                    "workflow_id": workflow.id,
                    "checkpoint_id": getattr(checkpoint, "checkpoint_id", ""),
                    "activation_attempt_id": getattr(
                        checkpoint, "activation_attempt_id", ""
                    ),
                },
            )

        request = checkpoint_request
        if (
            self.case_id == "R01"
            and self.controller.point == "after_run_active"
            and checkpoint_request is not None
        ):
            request = replace(
                checkpoint_request,
                interrupt_after_stage_id="r01-stage-1",
            )
        return await super().execute(
            workflow,
            context,
            checkpoint_request=request,
        )

    async def _execute_plan(self, plan: Any, task: Any, context: ExecutionContext) -> Any:
        result = await super()._execute_plan(plan, task, context)
        if result.success:
            _append_audit(
                self.audit_path,
                {
                    "pid": os.getpid(),
                    "task_id": task.id,
                    "workflow_id": context.workflow_id,
                    "event": "effect_committed",
                },
            )
            if self.case_id == "R02" and task.id == "r02-effect-write":
                target = context.get_var("workspace").resolve_path(
                    "output/effect.txt", must_exist=True
                )
                self.controller.observe(
                    "after_effect_commit",
                    {
                        "task_id": task.id,
                        "path": "output/effect.txt",
                        "size": target.stat().st_size,
                    },
                )
        return result


class CrashRunLauncher:
    def __init__(
        self,
        *,
        case_id: str,
        marker: Path,
        audit_path: Path,
    ) -> None:
        self.case_id = case_id
        self.controller = CrashController(marker)
        self.audit_path = audit_path
        self.error: dict[str, Any] | None = None

    def _coordinator(self, run_context: Any) -> RunResumeCoordinator:
        return RunResumeCoordinator(
            workflows=build_workflows(self.case_id),
            workflow_executor=InstrumentedWorkflowExecutor(
                case_id=self.case_id,
                controller=self.controller,
                audit_path=self.audit_path,
            ),
            runtime_store_view=run_context.durable_store_view,
        )

    @staticmethod
    def _execution_context(run_context: Any, workflow: Workflow) -> ExecutionContext:
        context = ExecutionContext(workflow_id=workflow.id)
        context.set_var("workspace", run_context.workspace)
        context.set_var("working_directory", str(run_context.workspace.root))
        return context

    async def start(
        self,
        *,
        session_context: Any,
        run_context: Any,
        request: StartRunRequest,
    ) -> None:
        del session_context
        try:
            view = run_context.durable_store_view
            if view is None:
                raise RuntimeError("P2-R requires a durable RunContext")
            view.transition_run_with_event(
                run_status="RUNNING",
                event_id=f"p2-r-start:{run_context.run_id}",
                event_type="run_started",
                timestamp=_timestamp(),
                payload={"case_id": self.case_id},
                expected_status="CREATED",
            )
            if view.get_run_index() is None:
                view.bootstrap_run_index(
                    build_index(
                        self.case_id,
                        run_id=run_context.run_id,
                        session_id=request.session_id,
                        user_id=request.user_id,
                    )
                )
            coordinator = self._coordinator(run_context)
            await self._drive_start(coordinator, run_context)
        except Exception as error:
            self.error = {
                "type": type(error).__name__,
                "code": str(getattr(getattr(error, "code", None), "value", "")),
                "message": str(error)[:300],
            }
            raise

    async def _drive_start(
        self,
        coordinator: RunResumeCoordinator,
        run_context: Any,
    ) -> None:
        def context_factory(workflow: Workflow) -> ExecutionContext:
            return self._execution_context(run_context, workflow)

        if self.case_id in {"R01", "R02", "R03"}:
            result = await coordinator.execute_or_resume(
                run_context.run_id,
                context_factory,
                attempt_id=f"attempt-{self.case_id.lower()}-a",
            )
            index = result.index
            if self.case_id == "R01":
                checkpoint = run_context.durable_store_view.latest_checkpoint(
                    workflow_id="wf-main"
                )
                self.controller.observe(
                    "after_run_active",
                    {
                        "active_workflow_id": index.active_workflow_id,
                        "checkpoint_id": getattr(checkpoint, "checkpoint_id", ""),
                        "completed_task_ids": list(
                            getattr(checkpoint, "completed_task_ids", ())
                        ),
                    },
                )
            elif self.case_id == "R03":
                checkpoint = run_context.durable_store_view.latest_checkpoint(
                    workflow_id="wf-event"
                )
                self.controller.observe(
                    "after_checkpoint_commit",
                    {
                        "checkpoint_id": getattr(checkpoint, "checkpoint_id", ""),
                        "completed_workflow_ids": list(index.completed_workflow_ids),
                    },
                )
            return

        first = await coordinator.execute_or_resume(
            run_context.run_id,
            context_factory,
            attempt_id="attempt-r04-a",
        )
        if "wf-a" not in first.index.completed_workflow_ids:
            raise RuntimeError("R04 failed to complete Workflow A before B activation")
        await coordinator.execute_or_resume(
            run_context.run_id,
            context_factory,
            attempt_id="attempt-r04-b",
        )

    async def resume(self, *, run_context: Any, request: ResumeRunRequest) -> None:
        try:
            view = run_context.durable_store_view
            if view is None:
                raise RuntimeError("P2-R resume requires a durable RunContext")
            view.transition_run_with_event(
                run_status="RUNNING",
                event_id=f"p2-r-resume:{run_context.run_id}:{request.request_id}",
                event_type="run_resumed",
                timestamp=_timestamp(),
                payload={"case_id": self.case_id},
            )
            coordinator = self._coordinator(run_context)

            def context_factory(workflow: Workflow) -> ExecutionContext:
                return self._execution_context(run_context, workflow)

            final = None
            for attempt in range(6):
                final = await coordinator.execute_or_resume(
                    run_context.run_id,
                    context_factory,
                    attempt_id=f"attempt-{self.case_id.lower()}-resume-{attempt}",
                )
                index = final.index
                if not index.active_workflow_id and not index.pending_workflow_ids:
                    break
                if final.execution_result is None:
                    break
            if final is None:
                raise RuntimeError("resume coordinator produced no result")
            index = final.index
            completed = (
                not index.active_workflow_id
                and not index.pending_workflow_ids
                and len(index.completed_workflow_ids) == len(index.workflow_sequence)
            )
            if not completed:
                raise RuntimeError(
                    "resume did not complete the durable Run: "
                    f"active={index.active_workflow_id} pending={index.pending_workflow_ids}"
                )
            # Durable workflow finalization publishes the terminal Run state
            # and event atomically.  Only adapters whose coordinator did not
            # do so need a Service-level terminal transition.
            if str(view.head().run_status).upper() != "COMPLETED":
                view.transition_run_with_event(
                    run_status="COMPLETED",
                    event_id=f"p2-r-complete:{run_context.run_id}",
                    event_type="run_completed",
                    timestamp=_timestamp(),
                    payload={"case_id": self.case_id},
                )
        except Exception as error:
            self.error = {
                "type": type(error).__name__,
                "code": str(getattr(getattr(error, "code", None), "value", "")),
                "message": str(error)[:300],
            }
            raise


def _request(case_id: str) -> StartRunRequest:
    return StartRunRequest(
        tenant_id="tenant-p2r",
        user_id="user-p2r",
        session_id="session-p2r",
        run_id=f"run-{case_id.lower()}",
        request_id=f"start-{case_id.lower()}",
        request_text=f"deterministic process crash case {case_id}",
    )


async def _start_worker(args: argparse.Namespace) -> int:
    _install_deterministic_executor()
    store = SqliteRuntimeStore.open(args.database)
    contexts = ServiceContextFactory(
        store,
        workspace_root=args.workspace,
        writer_id="writer-a",
    )
    launcher = CrashRunLauncher(
        case_id=args.case,
        marker=args.marker,
        audit_path=args.audit,
    )
    service = AgentService(
        runtime_store=store,
        launcher=launcher,
        context_factory=contexts,
    )
    request = _request(args.case)
    try:
        handle = await service.start_run(request)
        for _ in range(2_000):
            if launcher.error is not None:
                _write_json(args.result, {"phase": "start", "error": launcher.error})
                return 2
            snapshot = await service.get_run(
                RunLookupRequest(
                    tenant_id=request.tenant_id,
                    user_id=request.user_id,
                    session_id=request.session_id,
                    run_id=handle.run_id,
                    request_id=f"start-watch-{args.case.lower()}",
                )
            )
            if snapshot.status in TERMINAL:
                _write_json(
                    args.result,
                    {
                        "phase": "start",
                        "unexpected_terminal": snapshot.status.value,
                    },
                )
                return 3
            await asyncio.sleep(0.01)
        _write_json(args.result, {"phase": "start", "error": {"type": "Timeout"}})
        return 4
    finally:
        await service.close()


async def _resume_worker(args: argparse.Namespace) -> int:
    _install_deterministic_executor()
    store = SqliteRuntimeStore.open(args.database)
    contexts = ServiceContextFactory(
        store,
        workspace_root=args.workspace,
        writer_id="writer-b",
    )
    launcher = CrashRunLauncher(
        case_id=args.case,
        marker=args.marker,
        audit_path=args.audit,
    )
    service = AgentService(
        runtime_store=store,
        launcher=launcher,
        context_factory=contexts,
    )
    start = _request(args.case)
    request = ResumeRunRequest(
        tenant_id=start.tenant_id,
        user_id=start.user_id,
        session_id=start.session_id,
        run_id=str(start.run_id),
        request_id=f"resume-{args.case.lower()}",
        request_text=f"resume deterministic crash case {args.case}",
    )
    try:
        if args.case == "R03":
            snapshot = await service.get_run(
                RunLookupRequest(
                    tenant_id=request.tenant_id,
                    user_id=request.user_id,
                    session_id=request.session_id,
                    run_id=request.run_id,
                    request_id=f"rehydrate-{args.case.lower()}",
                )
            )
            _write_json(
                args.result,
                {
                    "phase": "rehydrate",
                    "status": snapshot.status.value,
                    "revision": snapshot.revision,
                },
            )
            return 0 if snapshot.status is RunStatus.COMPLETED else 7
        try:
            await service.resume_run(request)
        except Exception as error:
            _write_json(
                args.result,
                {
                    "phase": "resume",
                    "error": {
                        "type": type(error).__name__,
                        "code": str(
                            getattr(getattr(error, "code", None), "value", "")
                        ),
                        "message": str(error)[:300],
                    },
                },
            )
            return 5
        for _ in range(3_000):
            if launcher.error is not None:
                _write_json(args.result, {"phase": "resume", "error": launcher.error})
                return 6
            snapshot = await service.get_run(
                RunLookupRequest(
                    tenant_id=request.tenant_id,
                    user_id=request.user_id,
                    session_id=request.session_id,
                    run_id=request.run_id,
                    request_id=f"resume-watch-{args.case.lower()}",
                )
            )
            if snapshot.status in TERMINAL:
                _write_json(
                    args.result,
                    {
                        "phase": "resume",
                        "status": snapshot.status.value,
                        "revision": snapshot.revision,
                    },
                )
                return 0 if snapshot.status is RunStatus.COMPLETED else 7
            await asyncio.sleep(0.01)
        _write_json(args.result, {"phase": "resume", "error": {"type": "Timeout"}})
        return 8
    finally:
        await service.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="P2-R1 crash child worker")
    parser.add_argument("--mode", choices=("start", "resume"), required=True)
    parser.add_argument("--case", choices=tuple(CRASH_POINTS), required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--marker", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.mode == "start" and os.environ.get("TSAGENT_TEST_CRASH_POINT") != CRASH_POINTS[args.case]:
        raise SystemExit("start worker requires the case-specific crash point")
    return asyncio.run(_start_worker(args) if args.mode == "start" else _resume_worker(args))


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["CRASH_POINTS", "build_index", "build_workflows", "main"]
