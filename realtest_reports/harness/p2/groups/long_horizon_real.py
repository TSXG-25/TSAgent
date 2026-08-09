"""Real AgentService adapter for the P2-L long-horizon cases.

The adapter is deliberately separate from the deterministic fixture runner.
It records one attempt per case and converts public Service facts plus
instrumentation into the shared ``RunTraceEvidence`` model. It does not retry,
patch prompts, or decide that a failed capability is a Runtime failure.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from benchmarks.p2.cases import P2Case, P2Group, build_cases

from ..evidence import ArtifactEvidence, PerformanceEvidence, RunTraceEvidence
from ..report import LongHorizonResult, make_result


EXPECTED_ARTIFACTS: dict[str, tuple[str, ...]] = {
    "L01": ("output/p2_l01.md",),
    "L02": ("output/p2_l02_summary.md", "output/p2_l02_data.json"),
    "L03": ("output/p2_l03.md",),
    "L04": ("output/p2_l04_summary.md", "output/p2_l04_sources.json", "output/p2_l04_report.md"),
    "L05": ("output/p2_l05.md", "output/p2_l05_checklist.md"),
}


PROMPTS: dict[str, str] = {
    "L01": (
        "完成一个不少于10个有依赖关系的步骤的项目计划。每一步都要有明确结果，"
        "最后将完整总结保存为 output/p2_l01.md，并验证文件真实存在且非空。"
    ),
    "L02": (
        "完成一个包含分支和汇合的多步骤研究任务：分别整理两个独立分支，"
        "最后把汇总保存为 output/p2_l02_summary.md，把结构化数据保存为 "
        "output/p2_l02_data.json，并验证两个文件。"
    ),
    "L03": (
        "完成一个约10步的项目任务并保存 output/p2_l03.md。任务中若某一步失败，"
        "只允许有限次重规划，保留已经验证的进度，最后如实报告最终状态。"
    ),
    "L04": (
        "完成一个长链研究任务，必须同时生成并验证三个文件："
        "output/p2_l04_summary.md、output/p2_l04_sources.json、"
        "output/p2_l04_report.md。任何一个缺失都不能声称任务完成。"
    ),
    "L05": (
        "完成一个有依赖关系的长链任务，先保存主要结果到 output/p2_l05.md，"
        "再保存验证清单到 output/p2_l05_checklist.md；保留已完成进度并验证两个文件。"
    ),
}


class _CaptureRuntime:
    """UniversalAgent wrapper used only to retain its final evidence projection."""

    def __init__(self, *args: Any, evidence_sink: dict[str, dict[str, Any]], **kwargs: Any) -> None:
        from agent.runtime import UniversalAgent

        run_context = kwargs.get("run_context")
        self._run_id = str(getattr(run_context, "run_id", ""))
        self._sink = evidence_sink
        self._inner = UniversalAgent(*args, **kwargs)

    @property
    def last_run_evidence(self) -> dict[str, Any]:
        return dict(getattr(self._inner, "last_run_evidence", {}) or {})

    async def run(self, request_text: str) -> str:
        try:
            return await self._inner.run(request_text)
        finally:
            self._sink[self._run_id] = dict(self.last_run_evidence)

    def close(self) -> None:
        self._inner.close()


class _Instrumentation:
    """Small reversible instrumentation layer for one real harness process."""

    def __init__(self) -> None:
        self.tool_calls: list[dict[str, Any]] = []
        self.llm_calls = 0
        self.provider_ms = 0.0
        self.provider_errors: list[str] = []
        self.plan_calls = 0
        self.execution_stage_calls = 0
        self.task_counts: dict[str, int] = {}
        self.completed_tasks: set[str] = set()
        self._originals: list[tuple[Any, str, Any]] = []

    def install(self) -> None:
        import agent.executor.plan_executor as plan_executor
        import agent.llm as llm_module
        import agent.orchestrator.executor as execution_module
        import agent.orchestrator.main as orchestrator_module

        original_tool = plan_executor.PlanExecutor._exec_tool
        original_llm = llm_module.llm.ainvoke
        original_plan = orchestrator_module.ExecutionOrchestrator.plan
        original_execute = execution_module.ExecutionStage.run
        self._patch(plan_executor.PlanExecutor, "_exec_tool", self._tool_wrapper(original_tool))
        self._patch(llm_module.llm, "ainvoke", self._llm_wrapper(original_llm))
        self._patch(orchestrator_module.ExecutionOrchestrator, "plan", self._plan_wrapper(original_plan))
        self._patch(execution_module.ExecutionStage, "run", self._execute_wrapper(original_execute))

    def _patch(self, owner: Any, name: str, replacement: Any) -> None:
        self._originals.append((owner, name, getattr(owner, name)))
        setattr(owner, name, replacement)

    def _tool_wrapper(self, original: Any) -> Any:
        async def wrapped(
            owner: Any,
            tool_name: str,
            args: dict[str, Any],
            **kwargs: Any,
        ) -> Any:
            self.tool_calls.append({"tool": str(tool_name), "args": dict(args)})
            # P2-LH1 passes the scoped Run workspace through this method;
            # instrumentation must remain transparent to that production
            # contract.
            return await original(owner, tool_name, args, **kwargs)

        return wrapped

    def _llm_wrapper(self, original: Any) -> Any:
        async def wrapped(messages: Any, *args: Any, **kwargs: Any) -> Any:
            self.llm_calls += 1
            started = time.perf_counter()
            try:
                return await original(messages, *args, **kwargs)
            except Exception as error:
                self.provider_errors.append(type(error).__name__)
                raise
            finally:
                self.provider_ms += (time.perf_counter() - started) * 1000

        return wrapped

    def _plan_wrapper(self, original: Any) -> Any:
        async def wrapped(owner: Any, *args: Any, **kwargs: Any) -> Any:
            self.plan_calls += 1
            return await original(owner, *args, **kwargs)

        return wrapped

    def _execute_wrapper(self, original: Any) -> Any:
        async def wrapped(owner: Any, state: Any) -> Any:
            self.execution_stage_calls += 1
            for task in state.get("plan", []) if isinstance(state, dict) else []:
                if not isinstance(task, dict):
                    continue
                task_id = str(task.get("id", "")).strip()
                if task_id:
                    self.task_counts[task_id] = self.task_counts.get(task_id, 0) + 1
            result = await original(owner, state)
            if isinstance(result, tuple) and result and isinstance(result[0], dict):
                for task in result[0].get("plan", []) or []:
                    if isinstance(task, dict) and task.get("status") == "succeeded":
                        task_id = str(task.get("id", "")).strip()
                        if task_id:
                            self.completed_tasks.add(task_id)
            return result

        return wrapped

    def close(self) -> None:
        for owner, name, original in reversed(self._originals):
            setattr(owner, name, original)
        self._originals.clear()


def _digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _case_by_id(case_id: str) -> P2Case:
    for case in build_cases():
        if case.id == case_id:
            return case
    raise KeyError(case_id)


async def run_real_case(case_id: str, *, snapshot: Path | None = None, timeout: float = 600.0) -> LongHorizonResult:
    """Execute one L case once through the public AgentService boundary."""
    case = _case_by_id(case_id)
    if case.group is not P2Group.LONG_HORIZON:
        raise ValueError(f"{case_id} is not a Long-horizon case")
    if case_id not in PROMPTS:
        raise KeyError(f"no fixed prompt for {case_id}")

    from agent.bootstrap import load_all
    from agent.runtime_store import SqliteRuntimeStore
    from agent.service import (
        AgentService,
        EventStreamRequest,
        RunLookupRequest,
        StartRunRequest,
    )
    from agent.service.context_factory import ServiceContextFactory
    from agent.service.runtime_launcher import RuntimeExecutionLauncher

    load_all()
    root = Path(
        tempfile.mkdtemp(
            prefix=f"tsagent-p2-{case_id.lower()}-",
            dir=str(snapshot) if snapshot is not None else None,
        )
    )
    database = root / "runtime.sqlite3"
    workspace = root / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "output").mkdir(parents=True, exist_ok=True)
    evidence_sink: dict[str, dict[str, Any]] = {}
    instrumentation = _Instrumentation()
    store = SqliteRuntimeStore.open(database)

    def factory(*args: Any, **kwargs: Any) -> _CaptureRuntime:
        return _CaptureRuntime(*args, evidence_sink=evidence_sink, **kwargs)

    service = AgentService(
        runtime_store=store,
        launcher=RuntimeExecutionLauncher(runtime_factory=factory),
        context_factory=ServiceContextFactory(
            store,
            workspace_root=workspace,
            writer_id=f"p2-l-{case_id.lower()}",
        ),
    )
    request_id = f"p2-{case_id.lower()}"
    run_id = f"run-{case_id.lower()}"
    request = StartRunRequest(
        tenant_id="p2-tenant",
        user_id="p2-user",
        session_id=f"p2-session-{case_id.lower()}",
        request_id=request_id,
        run_id=run_id,
        request_text=PROMPTS[case_id],
    )
    instrumentation.install()
    started = time.perf_counter()
    try:
        handle = await service.start_run(request)
        deadline = time.monotonic() + timeout
        snapshot_result: Any = None
        while time.monotonic() < deadline:
            snapshot_result = await service.get_run(
                RunLookupRequest(
                    tenant_id=request.tenant_id,
                    user_id=request.user_id,
                    session_id=request.session_id,
                    run_id=handle.run_id,
                    request_id=f"{request_id}-snapshot",
                )
            )
            if snapshot_result.status.value in {"COMPLETED", "FAILED_TERMINAL", "BLOCKED", "CANCELLED"}:
                break
            await asyncio.sleep(1.0)
        if snapshot_result is None:
            raise TimeoutError("Run did not produce a Snapshot")
        events = [
            event
            async for event in service.stream_events(
                EventStreamRequest(
                    tenant_id=request.tenant_id,
                    user_id=request.user_id,
                    session_id=request.session_id,
                    run_id=handle.run_id,
                    request_id=f"{request_id}-events",
                    after_sequence=0,
                )
            )
        ]
        raw_runtime = evidence_sink.get(run_id, {})
        artifacts = []
        for relative in EXPECTED_ARTIFACTS[case_id]:
            path = workspace / relative
            if path.exists() and path.is_file():
                artifacts.append(
                    ArtifactEvidence(
                        artifact_id=relative,
                        digest=_digest(path),
                        verified=path.stat().st_size > 0,
                        exists=True,
                        producer="workspace-ground-truth",
                    )
                )
        terminal_events = [event for event in events if event.is_terminal]
        terminal_event = terminal_events[-1].event_type.value if terminal_events else ""
        workspace_root = workspace.resolve()
        leaked_to_process_root = any(
            (
                candidate := (Path.cwd() / relative).resolve()
            ).exists()
            and not candidate.is_relative_to(workspace_root)
            for relative in EXPECTED_ARTIFACTS[case_id]
        )
        trace = RunTraceEvidence(
            case_id=case_id,
            run_id=run_id,
            provider=os.environ.get("P2_PROVIDER_NAME", "primary"),
            planned_tasks=tuple(instrumentation.task_counts),
            workflow_transitions=tuple(event.event_type.value for event in events),
            task_execution_counts=dict(instrumentation.task_counts),
            completed_task_ids=tuple(sorted(instrumentation.completed_tasks)),
            artifacts=tuple(artifacts),
            required_artifact_ids=EXPECTED_ARTIFACTS[case_id],
            terminal_status=snapshot_result.status.value,
            terminal_event_type=terminal_event,
            terminal_outputs_verified=bool(raw_runtime.get("terminal_outputs_verified", False)),
            task_failures=tuple(
                str(item.get("id", ""))
                for item in raw_runtime.get("task_failures", [])
                if isinstance(item, dict)
            ),
            cross_context_leakage=leaked_to_process_root,
            provider_errors=tuple(instrumentation.provider_errors),
            performance=PerformanceEvidence(
                wall_ms=(time.perf_counter() - started) * 1000,
                provider_ms=instrumentation.provider_ms,
                llm_calls=instrumentation.llm_calls,
                replans=int(raw_runtime.get("timing", {}).get("replan_count", 0) or 0),
                tool_calls_count=len(instrumentation.tool_calls),
                time_to_first_event_ms=0.0,
                time_to_first_artifact_ms=0.0,
            ),
        )
        result = make_result(
            case,
            trace,
            capability_outcome="FAIL",
            capability_detail="real AgentService attempt; artifact ground truth collected from isolated workspace",
        )
        capability = (
            "PASS"
            if trace.terminal_status == "COMPLETED"
            and trace.terminal_outputs_verified
            and result.invariants.missing_required_artifacts == 0
            and not result.invariants.cross_context_leakage
            else "FAIL"
        )
        detail = "real AgentService attempt; artifact ground truth collected from isolated workspace"
        return make_result(case, trace, capability_outcome=capability, capability_detail=detail)
    finally:
        instrumentation.close()
        await service.close()
        import shutil

        shutil.rmtree(root, ignore_errors=True)


__all__ = ["EXPECTED_ARTIFACTS", "PROMPTS", "run_real_case"]
