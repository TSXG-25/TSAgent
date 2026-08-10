"""Isolated real-Provider worker for P2-P.

The parent launches one child per fixed case/provider.  The child installs a
single Provider before Runtime imports, executes each predefined probe once,
and writes secret-free evidence.  A missing Provider configuration is
reported by the parent as DEFERRED and never converted into a capability
failure.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Iterable, Mapping

from ..evidence import ArtifactEvidence, PerformanceEvidence, RunTraceEvidence
from ..provider_adapter import (
    FixedProviderRouter,
    ProviderSpec,
    default_provider_specs,
    install_fixed_provider,
)
from .portability import (
    AttemptStatus,
    PortabilityAttemptResult,
    PortabilityProbe,
    PortabilityProbeResult,
    PortabilityScenario,
    ProbeKind,
    deferred_attempt,
    materialize_fixtures,
    scenario_by_id,
)


class _CaptureRuntime:
    def __init__(
        self,
        *args: Any,
        evidence_sink: dict[str, dict[str, Any]],
        **kwargs: Any,
    ) -> None:
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
            self._sink[self._run_id] = self.last_run_evidence

    def close(self) -> None:
        self._inner.close()


class _Instrumentation:
    """Capture execution facts without retaining tool content or LLM prompts."""

    def __init__(self) -> None:
        self.tool_calls: list[dict[str, Any]] = []
        self.plan_calls = 0
        self.execution_stage_calls = 0
        self.task_counts: dict[str, int] = {}
        self.completed_tasks: set[str] = set()
        self._originals: list[tuple[Any, str, Any]] = []

    def install(self) -> None:
        import agent.executor.plan_executor as plan_executor
        import agent.orchestrator.executor as execution_module
        import agent.orchestrator.main as orchestrator_module

        original_tool = plan_executor.PlanExecutor._exec_tool
        original_plan = orchestrator_module.ExecutionOrchestrator.plan
        original_execute = execution_module.ExecutionStage.run
        self._patch(
            plan_executor.PlanExecutor,
            "_exec_tool",
            self._tool_wrapper(original_tool),
        )
        self._patch(
            orchestrator_module.ExecutionOrchestrator,
            "plan",
            self._plan_wrapper(original_plan),
        )
        self._patch(
            execution_module.ExecutionStage,
            "run",
            self._execute_wrapper(original_execute),
        )

    def _patch(self, owner: Any, name: str, replacement: Any) -> None:
        self._originals.append((owner, name, getattr(owner, name)))
        setattr(owner, name, replacement)

    @staticmethod
    def _safe_target(args: Mapping[str, Any]) -> str:
        for key in ("path", "destination", "dst", "target", "spec", "source", "src"):
            value = str(args.get(key, "") or "").strip()
            if value:
                candidate = Path(value)
                return candidate.name if candidate.is_absolute() else value[:240]
        return ""

    def _tool_wrapper(self, original: Any) -> Any:
        async def wrapped(
            owner: Any,
            tool_name: str,
            args: dict[str, Any],
            **kwargs: Any,
        ) -> Any:
            record = {
                "tool": str(tool_name),
                "target": self._safe_target(args),
                "success": False,
            }
            self.tool_calls.append(record)
            try:
                result = await original(owner, tool_name, args, **kwargs)
            except BaseException:
                raise
            else:
                record["success"] = True
                return result

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

    @property
    def duplicate_side_effect_count(self) -> int:
        effect_tools = {
            "filesystem.write",
            "filesystem.copy",
            "filesystem.move",
            "filesystem.delete",
        }
        counts: dict[tuple[str, str], int] = {}
        for call in self.tool_calls:
            if not call["success"] or call["tool"] not in effect_tools:
                continue
            key = (str(call["tool"]), str(call["target"]))
            counts[key] = counts.get(key, 0) + 1
        return sum(max(count - 1, 0) for count in counts.values())

    def close(self) -> None:
        for owner, name, original in reversed(self._originals):
            setattr(owner, name, original)
        self._originals.clear()


def _digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _fingerprint(path: Path) -> tuple[bool, str]:
    if not path.exists() or not path.is_file():
        return False, ""
    return True, _digest(path)


def _artifact_verified(probe: PortabilityProbe, relative: str, path: Path) -> bool:
    if not path.exists() or not path.is_file() or path.stat().st_size == 0:
        return False
    if probe.probe_id == "P02-multi" and relative.endswith("_data.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return False
        return value == ["alpha", "beta", "gamma"]
    return True


def _provider_errors(router: FixedProviderRouter, start_index: int) -> tuple[str, ...]:
    return tuple(
        call.error_code.value
        for call in router.recorder.calls[start_index:]
        if call.error_code is not None
    )


def _security_violation(
    answer: str,
    *,
    router: FixedProviderRouter,
    workspace: Path,
    database: Path,
) -> bool:
    secret = router.resolved.api_key
    return bool(
        (secret and secret in answer)
        or "Traceback (most recent call last)" in answer
        or str(workspace.resolve()) in answer
        or str(database.resolve()) in answer
    )


async def _execute_probe(
    scenario: PortabilityScenario,
    probe: PortabilityProbe,
    router: FixedProviderRouter,
    *,
    work_root: Path,
    timeout: float,
) -> PortabilityProbeResult:
    from agent.effect_truth import has_success_claim
    from agent.runtime_store import SqliteRuntimeStore
    from agent.service import AgentService, EventStreamRequest, RunLookupRequest, StartRunRequest
    from agent.service.context_factory import ServiceContextFactory
    from agent.service.runtime_launcher import RuntimeExecutionLauncher

    root = Path(
        tempfile.mkdtemp(
            prefix=f"{scenario.case.id.lower()}-{probe.probe_id.lower()}-",
            dir=str(work_root),
        )
    )
    database = root / "runtime.sqlite3"
    workspace = root / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "output").mkdir(parents=True, exist_ok=True)
    materialize_fixtures(probe, workspace)
    evidence_sink: dict[str, dict[str, Any]] = {}
    instrumentation = _Instrumentation()
    store = SqliteRuntimeStore.open(database)

    def runtime_factory(*args: Any, **kwargs: Any) -> _CaptureRuntime:
        return _CaptureRuntime(*args, evidence_sink=evidence_sink, **kwargs)

    service = AgentService(
        runtime_store=store,
        launcher=RuntimeExecutionLauncher(runtime_factory=runtime_factory),
        context_factory=ServiceContextFactory(
            store,
            workspace_root=workspace,
            writer_id=f"p2-p-{scenario.case.id.lower()}-{router.spec.variant}",
        ),
    )
    run_id = f"run-{scenario.case.id.lower()}-{router.spec.variant}-{probe.probe_id.lower()}"
    request_id = f"request-{scenario.case.id.lower()}-{router.spec.variant}-{probe.probe_id.lower()}"
    request = StartRunRequest(
        tenant_id="p2-portability",
        user_id="p2-user",
        session_id=f"session-{scenario.case.id.lower()}-{router.spec.variant}",
        request_id=request_id,
        run_id=run_id,
        request_text=probe.prompt,
    )
    global_before = {
        relative: _fingerprint((Path.cwd() / relative).resolve())
        for relative in probe.required_artifacts
    }
    call_start = len(router.recorder.calls)
    if probe.kind is ProbeKind.MALFORMED_STRUCTURED:
        router.arm_malformed_structured_probe()
    instrumentation.install()
    started = time.perf_counter()
    snapshot: Any = None
    events: list[Any] = []
    try:
        handle = await service.start_run(request)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            snapshot = await service.get_run(
                RunLookupRequest(
                    tenant_id=request.tenant_id,
                    user_id=request.user_id,
                    session_id=request.session_id,
                    run_id=handle.run_id,
                    request_id=f"{request_id}-snapshot",
                )
            )
            if snapshot.status.value in {
                "COMPLETED",
                "FAILED_TERMINAL",
                "BLOCKED",
                "CANCELLED",
            }:
                break
            await asyncio.sleep(0.25)
        if snapshot is None or snapshot.status.value not in {
            "COMPLETED",
            "FAILED_TERMINAL",
            "BLOCKED",
            "CANCELLED",
        }:
            raise TimeoutError("P2-P Run did not reach a terminal Snapshot")
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
        raw = evidence_sink.get(run_id, {})
        answer = str(raw.get("answer", "") or "")
        artifacts = tuple(
            ArtifactEvidence(
                artifact_id=relative,
                digest=_digest(path),
                verified=_artifact_verified(probe, relative, path),
                exists=True,
                producer="scoped-workspace-ground-truth",
            )
            for relative in probe.required_artifacts
            if (path := workspace / relative).exists() and path.is_file()
        )
        terminal_events = [event for event in events if event.is_terminal]
        terminal_event = (
            terminal_events[-1].event_type.value if terminal_events else ""
        )
        verified_effects = raw.get("verified_effects", []) or []
        success_claim = has_success_claim(answer)
        trace = RunTraceEvidence(
            case_id=scenario.case.id,
            run_id=run_id,
            provider=router.spec.provider_id,
            planned_tasks=tuple(instrumentation.task_counts),
            workflow_transitions=tuple(event.event_type.value for event in events),
            task_execution_counts=dict(instrumentation.task_counts),
            completed_task_ids=tuple(sorted(instrumentation.completed_tasks)),
            artifacts=artifacts,
            required_artifact_ids=probe.required_artifacts,
            terminal_status=snapshot.status.value,
            terminal_event_type=terminal_event,
            terminal_outputs_verified=bool(raw.get("terminal_outputs_verified", False)),
            task_failures=tuple(
                str(item.get("id", ""))
                for item in raw.get("task_failures", []) or []
                if isinstance(item, Mapping)
            ),
            duplicate_side_effect_count=instrumentation.duplicate_side_effect_count,
            cross_context_leakage=any(
                (
                    candidate := (Path.cwd() / relative).resolve()
                ).is_relative_to(workspace.resolve())
                is False
                and _fingerprint(candidate) != global_before[relative]
                for relative in probe.required_artifacts
            ),
            unsupported_effect_hallucination=bool(
                probe.kind is ProbeKind.UNSUPPORTED_EFFECT
                and (success_claim or snapshot.status.value == "COMPLETED")
                and not verified_effects
            ),
            security_violation=_security_violation(
                answer,
                router=router,
                workspace=workspace,
                database=database,
            ),
            provider_errors=_provider_errors(router, call_start),
            performance=PerformanceEvidence(
                wall_ms=(time.perf_counter() - started) * 1000,
                provider_ms=sum(
                    call.latency_ms for call in router.recorder.calls[call_start:]
                ),
                llm_calls=len(router.recorder.calls[call_start:]),
                replans=int(raw.get("timing", {}).get("replan_count", 0) or 0),
                tool_calls_count=len(instrumentation.tool_calls),
            ),
        )
        execution_truth = {
            "effect_truth_ok": bool(raw.get("effect_truth_ok", True)),
            "required_effects": list(raw.get("required_effects", []) or []),
            "verified_effects": list(verified_effects),
            "unsupported_effects": list(raw.get("unsupported_effects", []) or []),
            "failed_effects": list(raw.get("failed_effects", []) or []),
            "answer_sha256": hashlib.sha256(answer.encode("utf-8")).hexdigest(),
            "success_claim": success_claim,
            "tool_calls": [
                {
                    "tool": str(call["tool"]),
                    "target": str(call["target"]),
                    "success": bool(call["success"]),
                }
                for call in instrumentation.tool_calls
            ],
        }
        return PortabilityProbeResult.from_trace(
            scenario.case,
            probe,
            trace,
            failure_code=str(raw.get("failure_code", "") or ""),
            execution_truth=execution_truth,
        )
    finally:
        instrumentation.close()
        await service.close()
        shutil.rmtree(root, ignore_errors=True)


async def execute_real_attempt(
    scenario: PortabilityScenario,
    spec: ProviderSpec,
    *,
    work_root: Path,
    timeout: float = 600.0,
) -> PortabilityAttemptResult:
    router = FixedProviderRouter(spec)
    install_fixed_provider(router)
    # Runtime consumers capture ``agent.llm.llm`` during bootstrap, so the
    # fixed Provider must be installed before this import/call.
    from agent.bootstrap import load_all

    load_all()
    probes = tuple(
        [
            await _execute_probe(
                scenario,
                probe,
                router,
                work_root=work_root,
                timeout=timeout,
            )
            for probe in scenario.probes
        ]
    )
    return PortabilityAttemptResult(
        scenario=scenario,
        provider=spec.to_public_dict(),
        status=AttemptStatus.EXECUTED,
        probes=probes,
        provider_evidence=router.public_evidence(),
    )


def _invalid_attempt(
    scenario: PortabilityScenario,
    spec: ProviderSpec,
    reason: str,
) -> PortabilityAttemptResult:
    return PortabilityAttemptResult(
        scenario=scenario,
        provider=spec.to_public_dict(),
        status=AttemptStatus.INVALID,
        deferral_reason=reason,
        provider_evidence={"harness_error": reason},
    )


def _child_command(
    scenario: PortabilityScenario,
    spec_path: Path,
    result_path: Path,
    work_root: Path,
    timeout: float,
) -> list[str]:
    return [
        sys.executable,
        "-B",
        "-m",
        "realtest_reports.harness.p2.groups.portability_worker",
        "--child",
        "--case",
        scenario.case.id,
        "--spec",
        str(spec_path),
        "--result",
        str(result_path),
        "--work-root",
        str(work_root),
        "--timeout",
        str(timeout),
    ]


def run_real_matrix(
    scenarios: Iterable[PortabilityScenario],
    specs: Iterable[ProviderSpec],
    *,
    work_root: Path,
    timeout: float = 600.0,
) -> tuple[PortabilityAttemptResult, ...]:
    """Run each configured pair exactly once in independent child processes."""

    from dotenv import load_dotenv

    load_dotenv()
    root = work_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    attempts: list[PortabilityAttemptResult] = []
    for scenario in scenarios:
        for spec in specs:
            if not spec.resolve().available:
                attempts.append(deferred_attempt(scenario, spec))
                continue
            attempt_root = root / f"{scenario.case.id.lower()}-{spec.variant}"
            attempt_root.mkdir(parents=True, exist_ok=True)
            spec_path = attempt_root / "provider-spec.json"
            result_path = attempt_root / "attempt.json"
            spec_path.write_text(
                json.dumps(spec.to_config_dict(), sort_keys=True) + "\n",
                encoding="utf-8",
            )
            try:
                process = subprocess.run(
                    _child_command(
                        scenario,
                        spec_path,
                        result_path,
                        attempt_root,
                        timeout,
                    ),
                    cwd=str(Path(__file__).resolve().parents[4]),
                    text=True,
                    capture_output=True,
                    timeout=timeout * max(len(scenario.probes), 1) + 60.0,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                attempts.append(_invalid_attempt(scenario, spec, "HARNESS_TIMEOUT"))
                continue
            if process.returncode != 0 or not result_path.exists():
                attempts.append(
                    _invalid_attempt(
                        scenario,
                        spec,
                        f"HARNESS_CHILD_EXIT_{process.returncode}",
                    )
                )
                continue
            try:
                payload = json.loads(result_path.read_text(encoding="utf-8"))
                attempts.append(PortabilityAttemptResult.from_dict(payload))
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                attempts.append(_invalid_attempt(scenario, spec, "HARNESS_RESULT_INVALID"))
    return tuple(attempts)


def _load_spec(path: Path) -> ProviderSpec:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise TypeError("Provider spec must be a JSON object")
    return ProviderSpec.from_config_dict(value)


def _write_attempt(attempt: PortabilityAttemptResult, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(attempt.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="P2-P isolated Provider worker")
    parser.add_argument("--child", action="store_true")
    parser.add_argument("--case", required=True)
    parser.add_argument("--spec", required=True)
    parser.add_argument("--result", required=True)
    parser.add_argument("--work-root", required=True)
    parser.add_argument("--timeout", type=float, default=600.0)
    args = parser.parse_args()
    if not args.child:
        parser.error("portability_worker is child-only")
    from dotenv import load_dotenv

    load_dotenv()
    scenario = scenario_by_id(args.case)
    spec = _load_spec(Path(args.spec))
    attempt = asyncio.run(
        execute_real_attempt(
            scenario,
            spec,
            work_root=Path(args.work_root),
            timeout=args.timeout,
        )
    )
    _write_attempt(attempt, Path(args.result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["execute_real_attempt", "main", "run_real_matrix"]
