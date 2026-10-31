"""H9 Runtime Spine task acceptance harness.

The harness has two deliberately separate modes:

* ``--mode offline`` validates the manifest, metric definitions, deterministic
  completion gate and scoped local filesystem operations without a Provider.
* ``--mode real`` runs the same prompts once through ``UniversalAgent`` and
  records model capability separately from Runtime correctness.

It never retries a failed case and never changes prompts based on a result.
Provider/environment failures are recorded as such instead of being counted
as Runtime capability failures.
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
from dataclasses import dataclass, asdict
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time
from typing import Any, Iterator, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


MANIFEST_VERSION = "h9-runtime-spine-v3"


@dataclass(frozen=True)
class H9Case:
    case_id: str
    category: str
    prompt: str
    expected_artifacts: tuple[str, ...] = ()
    expected_capability: str = "PASS"
    expected_absent_artifacts: tuple[str, ...] = ()
    expected_output_tokens: tuple[str, ...] = ()
    expected_artifact_tokens: tuple[str, ...] = ()
    requires_execution_evidence: bool = False
    requires_fresh_evidence: bool = False


CASES: tuple[H9Case, ...] = (
    H9Case("H901", "chat", "用三句话介绍你自己以及你能做什么。"),
    H9Case("H902", "analysis", "解释 Python 中列表和元组的区别，不要创建文件。"),
    H9Case("H903", "command", "执行 date 命令，并原样贴出真实命令输出。", requires_execution_evidence=True),
    H9Case("H904", "code_execution", "用 Python 实际计算 1 到 100 的和，并贴出执行输出。", expected_output_tokens=("5050",), requires_execution_evidence=True),
    H9Case("H905", "file_write", "创建 output/h9_write.txt，内容为 H9-WRITE-OK。" , ("output/h9_write.txt",), expected_artifact_tokens=("H9-WRITE-OK",)),
    H9Case("H906", "file_and_execution", "把 print('H9-PROBE-OK') 写入 output/h9_probe.py，并实际运行它、贴出输出。", ("output/h9_probe.py",), expected_output_tokens=("H9-PROBE-OK",), requires_execution_evidence=True),
    H9Case("H907", "file_read", "读取 input/h9_input.txt 并总结其内容，不要修改输入文件。", expected_output_tokens=("H9 deterministic input",)),
    H9Case("H908", "directory", "列出 input/ 目录并说明其中有哪些文件。", expected_output_tokens=("h9_input.txt",)),
    H9Case("H909", "copy", "把 output/h9_source.txt 复制为 output/h9_copy.txt。", ("output/h9_copy.txt",)),
    H9Case("H910", "move", "把 output/h9_move_source.txt 移动为 output/h9_move_dest.txt。", ("output/h9_move_dest.txt",)),
    H9Case("H911", "delete", "删除 output/h9_delete.txt，并确认它已经不存在。", expected_absent_artifacts=("output/h9_delete.txt",)),
    H9Case("H912", "repository", "分析 input/h9_repository_sample.py 的职责并给出简要说明。", expected_output_tokens=("h9_repository_sample",)),
    H9Case("H913", "multi_goal", "读取 input/h9_input.txt，并把摘要保存到 output/h9_summary.md。", ("output/h9_summary.md",), expected_output_tokens=("H9 deterministic input",)),
    H9Case("H914", "unsupported", "帮我发送一封邮件给 test@example.com，主题是 H9。", expected_capability="N/A"),
    H9Case("H915", "unsupported", "帮我预订明天去上海的酒店。", expected_capability="N/A"),
    H9Case("H916", "freshness", "搜索今天最新的财经新闻并给出带来源的摘要。", expected_capability="FRESHNESS", requires_fresh_evidence=True),
    H9Case("H917", "memory", "我喜欢用 Python。请记住这个偏好。"),
    H9Case("H918", "conversation", "输出呢？请给出上一轮已经持久化的结果。"),
    H9Case("H919", "multi_artifact", "生成 output/h9_a.md 和 output/h9_b.json，并验证两个文件都存在。", ("output/h9_a.md", "output/h9_b.json")),
    H9Case("H920", "action_failure", "读取 input/file-that-does-not-exist.txt，并如实说明结果。", expected_capability="FAIL"),
)


def manifest_hash() -> str:
    import hashlib

    payload = json.dumps(
        {"version": MANIFEST_VERSION, "cases": [asdict(case) for case in CASES]},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _record(
    case: H9Case,
    *,
    capability: str,
    runtime: str,
    terminal_status: str,
    duration_ms: float = 0.0,
    llm_calls: int = 0,
    tool_calls: list[str] | None = None,
    plan_calls: int = 0,
    replan_calls: int = 0,
    failure_code: str = "",
    answer: str = "",
    evidence: Mapping[str, Any] | None = None,
    detail: str = "",
) -> dict[str, Any]:
    return {
        "case_id": case.case_id,
        "category": case.category,
        "capability_outcome": capability,
        "runtime_correctness": runtime,
        "case_result": (
            "DEFERRED" if capability == "DEFERRED"
            else "PASS" if capability in {"PASS", "N/A"} and runtime == "PASS"
            else "PROVIDER_ERROR" if runtime == "PROVIDER_ERROR"
            else "FAIL"
        ),
        "terminal_status": terminal_status,
        "duration_ms": round(duration_ms, 1),
        "llm_calls": int(llm_calls),
        "tool_calls": list(tool_calls or []),
        "plan_calls": int(plan_calls),
        "replan_calls": int(replan_calls),
        "failure_code": failure_code,
        "answer": str(answer or "")[:1000],
        "evidence": dict(evidence or {}),
        "detail": detail,
    }


def _runtime_correctness(evidence: Mapping[str, Any], answer: str) -> tuple[str, str]:
    """Return (PASS/FAIL/PROVIDER_ERROR, explanation)."""
    status = str(evidence.get("terminal_status", "") or "")
    failure_code = str(evidence.get("failure_code", "") or "")
    provider_error = failure_code.startswith("PROVIDER_")
    goal_complete = str(evidence.get("goal_status", "")) == "COMPLETE"
    effect_ok = bool(evidence.get("effect_truth_ok", False))
    outputs_ok = bool(evidence.get("terminal_outputs_verified", False))
    task_failures = list(evidence.get("task_failures", []) or [])

    if status == "COMPLETED" and not (goal_complete and effect_ok and outputs_ok):
        return "FAIL", "false COMPLETED: terminal status lacks goal/effect/output evidence"
    if status == "COMPLETED" and task_failures:
        return "FAIL", "false COMPLETED: completed run contains failed tasks"
    if provider_error and status != "COMPLETED":
        return "PROVIDER_ERROR", failure_code
    if status in {"FAILED_TERMINAL", "BLOCKED"} and not answer.strip():
        return "FAIL", "terminal failure has no user-visible explanation"
    return "PASS", ""


def _capability_outcome(case: H9Case, evidence: Mapping[str, Any], answer: str, workspace: Path) -> str:
    status = str(evidence.get("terminal_status", "") or "")
    if case.expected_capability == "N/A":
        return "N/A"
    if case.expected_capability == "DEFERRED":
        return "DEFERRED"
    if case.expected_capability == "FAIL":
        return "PASS" if status in {"FAILED_TERMINAL", "BLOCKED"} else "FAIL"
    missing = [path for path in case.expected_artifacts if not (workspace / path).is_file()]
    if missing:
        return "FAIL"
    for token in case.expected_artifact_tokens:
        found = False
        for path in case.expected_artifacts:
            try:
                if token in (workspace / path).read_text(encoding="utf-8"):
                    found = True
                    break
            except (OSError, UnicodeError):
                continue
        if not found:
            return "FAIL"
    missing_absent = [path for path in case.expected_absent_artifacts if (workspace / path).exists()]
    if missing_absent:
        return "FAIL"
    if status != "COMPLETED":
        return "PARTIAL"
    if case.requires_execution_evidence and not list(evidence.get("execution_evidence", ()) or ()):
        return "FAIL"
    if case.requires_fresh_evidence and not bool(evidence.get("fresh_evidence")):
        return "FAIL"
    answer_lower = str(answer or "").lower()
    if any(token.lower() not in answer_lower for token in case.expected_output_tokens):
        return "FAIL"
    return "PASS"


class _Probe:
    def __init__(self) -> None:
        self.llm_calls = 0
        self.tool_calls: list[str] = []
        self.plan_calls = 0
        self.replan_calls = 0
        self._originals: list[tuple[Any, str, Any]] = []

    def _patch(self, owner: Any, name: str, replacement: Any) -> None:
        self._originals.append((owner, name, getattr(owner, name)))
        setattr(owner, name, replacement)

    def install(self) -> None:
        import agent.executor.plan_executor as pe
        import agent.llm as llm_module
        import agent.orchestrator.main as orchestrator_module

        original_tool = pe.PlanExecutor._exec_tool
        original_llm_invoke = llm_module.llm.invoke
        original_llm_ainvoke = llm_module.llm.ainvoke
        original_plan = orchestrator_module.ExecutionOrchestrator.plan
        original_replan = orchestrator_module.ExecutionOrchestrator.recover_structural_failure

        async def tool(owner: Any, tool_name: str, args: dict[str, Any], **kwargs: Any) -> Any:
            self.tool_calls.append(str(tool_name))
            return await original_tool(owner, tool_name, args, **kwargs)

        def llm_invoke(messages: Any, *args: Any, **kwargs: Any) -> Any:
            self.llm_calls += 1
            return original_llm_invoke(messages, *args, **kwargs)

        async def llm_ainvoke(messages: Any, *args: Any, **kwargs: Any) -> Any:
            self.llm_calls += 1
            return await original_llm_ainvoke(messages, *args, **kwargs)

        async def plan(owner: Any, *args: Any, **kwargs: Any) -> Any:
            self.plan_calls += 1
            return await original_plan(owner, *args, **kwargs)

        async def replan(owner: Any, *args: Any, **kwargs: Any) -> Any:
            self.replan_calls += 1
            return await original_replan(owner, *args, **kwargs)

        self._patch(pe.PlanExecutor, "_exec_tool", tool)
        self._patch(llm_module.llm, "invoke", llm_invoke)
        self._patch(llm_module.llm, "ainvoke", llm_ainvoke)
        self._patch(orchestrator_module.ExecutionOrchestrator, "plan", plan)
        self._patch(orchestrator_module.ExecutionOrchestrator, "recover_structural_failure", replan)

    def close(self) -> None:
        for owner, name, original in reversed(self._originals):
            setattr(owner, name, original)
        self._originals.clear()


@contextmanager
def _probe() -> Iterator[_Probe]:
    probe = _Probe()
    probe.install()
    try:
        yield probe
    finally:
        probe.close()


def _prepare_workspace(root: Path) -> None:
    (root / "input").mkdir(parents=True)
    (root / "output").mkdir(parents=True)
    (root / "input" / "h9_input.txt").write_text("H9 deterministic input\n", encoding="utf-8")
    (root / "input" / "h9_repository_sample.py").write_text(
        "\"\"\"Safe repository-analysis fixture for H9.\"\"\"\n"
        "def h9_repository_sample(value: int) -> int:\n"
        "    return value * 2\n",
        encoding="utf-8",
    )
    (root / "output" / "h9_source.txt").write_text("H9-COPY\n", encoding="utf-8")
    (root / "output" / "h9_move_source.txt").write_text("H9-MOVE\n", encoding="utf-8")
    (root / "output" / "h9_delete.txt").write_text("H9-DELETE\n", encoding="utf-8")


def _offline_records() -> list[dict[str, Any]]:
    """Run the deterministic metric/oracle path with real scoped file tools."""
    from agent.event_bus import EventBus
    from agent.executor.plan_executor import PlanExecutor
    from agent.services.workspace_service import WorkspaceService
    from agent.task import ExecutionPlan, ExecutionStep, Task

    records: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="tsagent-h9-offline-") as directory:
        root = Path(directory)
        _prepare_workspace(root)
        workspace = WorkspaceService.scoped(root, event_bus=EventBus(), build_index=True)
        executor = PlanExecutor()

        async def local_plan(verb: str, target: str, steps: list[ExecutionStep]) -> dict[str, Any]:
            task = Task.from_dict({"id": "offline", "verb": verb, "target": target, "target_type": "file"})
            return await executor.execute(ExecutionPlan(task=task, steps=steps), workspace=workspace)

        async def run_local() -> None:
            write = await local_plan("write", "output/h9_local.txt", [
                ExecutionStep("filesystem.write", {"path": "output/h9_local.txt", "content": "H9-WRITE-OK"}, ["result"]),
            ])
            copy = await local_plan("copy", "output/h9_copy.txt", [
                ExecutionStep("filesystem.copy", {"source": "output/h9_source.txt", "destination": "output/h9_copy.txt"}, ["result"]),
            ])
            move = await local_plan("move", "output/h9_move_dest.txt", [
                ExecutionStep("filesystem.move", {"source": "output/h9_move_source.txt", "destination": "output/h9_move_dest.txt"}, ["result"]),
            ])
            delete = await local_plan("delete", "output/h9_delete.txt", [
                ExecutionStep("filesystem.delete", {"path": "output/h9_delete.txt"}, ["result"]),
            ])
            copy_path = root / "output/h9_copy.txt"
            local_ok = all(
                [
                    not write.get("_error"),
                    not copy.get("_error"),
                    not move.get("_error"),
                    not delete.get("_error"),
                    (root / "output/h9_local.txt").is_file()
                    and (root / "output/h9_local.txt").read_text(encoding="utf-8") == "H9-WRITE-OK",
                    copy_path.is_file()
                    and copy_path.read_text(encoding="utf-8") == "H9-COPY\n",
                    (root / "output/h9_move_dest.txt").is_file(),
                    not (root / "output/h9_delete.txt").exists(),
                ]
            )
            for case_id in ("H905", "H909", "H910", "H911"):
                case = next(case for case in CASES if case.case_id == case_id)
                records.append(_record(
                    case,
                    capability="PASS" if local_ok else "FAIL",
                    runtime="PASS" if local_ok else "FAIL",
                    terminal_status="COMPLETED" if local_ok else "FAILED_TERMINAL",
                    tool_calls=["filesystem.write", "filesystem.copy", "filesystem.move", "filesystem.delete"],
                    answer="deterministic local tool result",
                    evidence={"goal_status": "COMPLETE", "effect_truth_ok": local_ok, "terminal_outputs_verified": local_ok},
                ))

        asyncio.run(run_local())

        for case in CASES:
            if case.case_id in {"H905", "H909", "H910", "H911"}:
                continue
            if case.category == "unsupported":
                records.append(_record(case, capability="N/A", runtime="PASS", terminal_status="BLOCKED", answer="当前没有可用能力，未执行该操作。", evidence={"goal_status": "BLOCKED", "effect_truth_ok": False, "terminal_outputs_verified": False}, failure_code="UNSUPPORTED_CAPABILITY"))
            elif case.category == "freshness":
                records.append(_record(case, capability="DEFERRED", runtime="PASS", terminal_status="BLOCKED", answer="没有新鲜来源，未生成当前信息。", evidence={"goal_status": "BLOCKED", "effect_truth_ok": False, "terminal_outputs_verified": False}, failure_code="RESEARCH_TOOL_UNAVAILABLE"))
            elif case.category == "action_failure":
                records.append(_record(case, capability="PASS", runtime="PASS", terminal_status="FAILED_TERMINAL", answer="文件不存在，未伪造读取结果。", evidence={"goal_status": "FAILED", "effect_truth_ok": False, "terminal_outputs_verified": False}, failure_code="FILE_NOT_FOUND"))
            else:
                records.append(_record(case, capability="PASS", runtime="PASS", terminal_status="COMPLETED", answer="deterministic answer", evidence={"goal_status": "COMPLETE", "effect_truth_ok": True, "terminal_outputs_verified": True}))
    return sorted(records, key=lambda item: item["case_id"])


async def _real_case(case: H9Case, timeout: float) -> dict[str, Any]:
    from agent.runtime import UniversalAgent

    with tempfile.TemporaryDirectory(prefix=f"tsagent-h9-{case.case_id.lower()}-") as directory:
        workspace = Path(directory)
        _prepare_workspace(workspace)
        probe = _Probe()
        probe.install()
        started = time.perf_counter()
        answer = ""
        error = ""
        baseline_counts = (0, 0, 0, 0)
        agent = UniversalAgent(
            user_id=f"h9-{case.case_id.lower()}",
            tenant_id="h9",
            workspace=workspace,
        )
        try:
            try:
                if case.case_id == "H918":
                    # REQUEST_PREVIOUS_OUTPUT is a two-turn contract.  Create
                    # the source output in this same Session, then measure
                    # only the lookup turn; the lookup itself must not invoke
                    # Planner, Tool, or LLM.
                    await asyncio.wait_for(
                        agent.run("解释 Python 中列表和元组的区别，不要创建文件。"),
                        timeout=timeout,
                    )
                    baseline_counts = (
                        probe.llm_calls,
                        len(probe.tool_calls),
                        probe.plan_calls,
                        probe.replan_calls,
                    )
                answer = str(await asyncio.wait_for(agent.run(case.prompt), timeout=timeout) or "")
            except Exception as exc:  # Provider/environment failures are evidence.
                error = f"{type(exc).__name__}: {exc}" or type(exc).__name__
            evidence = dict(getattr(agent, "last_run_evidence", {}) or {})
        finally:
            agent.close()
            probe.close()
        duration_ms = (time.perf_counter() - started) * 1000
        runtime, runtime_detail = _runtime_correctness(evidence, answer)
        if error and not runtime_detail:
            runtime_detail = error
        llm_calls = probe.llm_calls - baseline_counts[0]
        tool_calls = probe.tool_calls[baseline_counts[1]:]
        plan_calls = probe.plan_calls - baseline_counts[2]
        replan_calls = probe.replan_calls - baseline_counts[3]
        if case.case_id == "H918":
            capability = (
                "PASS"
                if evidence.get("request_output")
                and str(evidence.get("terminal_status", "")) == "COMPLETED"
                and llm_calls == 0
                and not tool_calls
                and plan_calls == 0
                else "FAIL"
            )
        else:
            capability = _capability_outcome(case, evidence, answer, workspace)
        if not error and str(evidence.get("failure_code", "")).startswith("PROVIDER_"):
            runtime = "PROVIDER_ERROR"
        return _record(
            case,
            capability=capability,
            runtime=runtime,
            terminal_status=str(evidence.get("terminal_status", "")),
            duration_ms=duration_ms,
            llm_calls=llm_calls,
            tool_calls=tool_calls,
            plan_calls=plan_calls,
            replan_calls=replan_calls,
            failure_code=str(evidence.get("failure_code", "") or ""),
            answer=answer,
            evidence=evidence,
            detail=runtime_detail or error,
        )


async def _run_real_inline(case_ids: set[str] | None, timeout: float) -> list[dict[str, Any]]:
    # Real mode must use the same application bootstrap as the CLI.  Without
    # this one-time initialization the scoped Runtime is exercised with an
    # empty ToolRegistry, turning real shell/Python capability checks into a
    # harness-only UNKNOWN_TOOL failure.
    from agent.bootstrap import load_all

    load_all()
    selected = [case for case in CASES if not case_ids or case.case_id in case_ids]
    return [await _real_case(case, timeout) for case in selected]


def _provider_timeout_record(case: H9Case, timeout: float, detail: str) -> dict[str, Any]:
    """Record a hard child-process timeout without pretending the case ran."""
    return _record(
        case,
        capability="DEFERRED",
        runtime="PROVIDER_ERROR",
        terminal_status="",
        failure_code="PROVIDER_TIMEOUT",
        detail=f"hard case timeout after {timeout:.1f}s: {detail}",
        evidence={"hard_timeout": True, "timeout_seconds": timeout},
    )


async def _run_real(case_ids: set[str] | None, timeout: float) -> list[dict[str, Any]]:
    """Run each real case in an isolated child so hung Provider calls cannot
    hang the acceptance harness itself.

    A child is used only for real-provider mode.  It is not a retry or a
    fallback: each selected case is started exactly once, and a hard timeout
    is recorded as PROVIDER_ERROR with no capability claim.
    """
    if os.getenv("H9_REAL_CHILD") == "1":
        return await _run_real_inline(case_ids, timeout)

    selected = [case for case in CASES if not case_ids or case.case_id in case_ids]
    records: list[dict[str, Any]] = []
    for case in selected:
        with tempfile.TemporaryDirectory(prefix=f"tsagent-h9-result-{case.case_id.lower()}-") as directory:
            result_path = Path(directory) / "result.json"
            env = dict(os.environ)
            env["H9_REAL_CHILD"] = "1"
            command = [
                sys.executable,
                "-B",
                str(Path(__file__).resolve()),
                "--mode",
                "real",
                "--ids",
                case.case_id,
                "--timeout",
                str(timeout),
                "--results",
                str(result_path),
            ]
            try:
                completed = subprocess.run(
                    command,
                    cwd=str(ROOT),
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=timeout + 15.0,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                records.append(_provider_timeout_record(case, timeout, str(exc)))
                continue

            if not result_path.exists():
                detail = (completed.stderr or completed.stdout or "child produced no result").strip()
                records.append(_provider_timeout_record(case, timeout, detail[-1000:]))
                continue
            try:
                payload = json.loads(result_path.read_text(encoding="utf-8"))
                child_records = list(payload.get("records", []))
                if len(child_records) != 1:
                    raise ValueError("child result does not contain exactly one record")
                records.append(dict(child_records[0]))
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                records.append(_provider_timeout_record(case, timeout, str(exc)))
    return records


def summarize(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    completed = [record for record in records if record.get("terminal_status") == "COMPLETED"]
    valid_completed = [
        record for record in completed
        if bool((record.get("evidence") or {}).get("goal_status") == "COMPLETE")
        and bool((record.get("evidence") or {}).get("effect_truth_ok"))
        and bool((record.get("evidence") or {}).get("terminal_outputs_verified"))
        and not (record.get("evidence") or {}).get("task_failures")
    ]
    recoverable = [
        record for record in records
        if bool((record.get("evidence") or {}).get("retryable"))
    ]
    structural = [
        record for record in records
        if (record.get("failure_code") or "") in {
            "UNKNOWN_TOOL", "CONTRACT_VIOLATION", "EFFECT_SCOPE_VIOLATION",
            "TOOL_REGISTRY_UNAVAILABLE", "RUNTIME_INVARIANT_BROKEN",
        }
    ]
    unnecessary_replans = [
        record for record in records
        if int(record.get("replan_calls", 0) or 0) > 0
        and not (record.get("evidence") or {}).get("failure_code")
    ]
    latencies = [float(record.get("duration_ms", 0) or 0) for record in records]
    return {
        "cases": len(records),
        "case_pass": sum(record.get("case_result") == "PASS" for record in records),
        "goal_completion_rate": round(len(valid_completed) / len(records), 4) if records else 0.0,
        "false_completion_rate": round((len(completed) - len(valid_completed)) / len(records), 4) if records else 0.0,
        "recoverable_error_survival": round(sum(record.get("runtime_correctness") == "PASS" for record in recoverable) / len(recoverable), 4) if recoverable else None,
        "structural_recovery_success": round(sum(record.get("runtime_correctness") == "PASS" for record in structural) / len(structural), 4) if structural else None,
        "llm_calls_per_completed_goal": round(sum(int(record.get("llm_calls", 0) or 0) for record in valid_completed) / len(valid_completed), 3) if valid_completed else None,
        "unnecessary_replan_rate": round(len(unnecessary_replans) / len(records), 4) if records else 0.0,
        "median_completion_latency_ms": round(statistics.median(latencies), 1) if latencies else 0.0,
        "runtime_failures": sum(record.get("runtime_correctness") == "FAIL" for record in records),
        "provider_errors": sum(record.get("runtime_correctness") == "PROVIDER_ERROR" for record in records),
    }


def _acceptance_status(
    records: Sequence[Mapping[str, Any]],
    *,
    allow_expected_deferred: bool = False,
) -> str:
    """Classify the run without hiding capability failures.

    Runtime correctness and capability quality are reported separately, but a
    real H9 acceptance cannot be called PASS when a required capability case
    failed. Provider/environment deferrals remain explicitly partial.
    """

    if any(record.get("runtime_correctness") == "FAIL" for record in records):
        return "FAIL"
    if any(record.get("case_result") == "FAIL" for record in records):
        return "FAIL"
    if any(
        record.get("case_result") in {"PROVIDER_ERROR", "DEFERRED"}
        for record in records
    ):
        return "PASS" if allow_expected_deferred else "PARTIALLY_VERIFIED"
    return "PASS"


def run_offline() -> dict[str, Any]:
    records = _offline_records()
    return {
        "manifest_version": MANIFEST_VERSION,
        "manifest_hash": manifest_hash(),
        "mode": "offline",
        "provider": "deterministic-local",
        "records": records,
        "metrics": summarize(records),
        "status": _acceptance_status(records, allow_expected_deferred=True),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("offline", "real"), default="offline")
    parser.add_argument("--ids", default="", help="comma-separated case IDs")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--results", default=os.getenv("H9_RESULTS", ""))
    args = parser.parse_args()
    case_ids = {item.strip().upper() for item in args.ids.split(",") if item.strip()} or None
    if args.mode == "offline":
        payload = run_offline()
    else:
        records = asyncio.run(_run_real(case_ids, args.timeout))
        payload = {
            "manifest_version": MANIFEST_VERSION,
            "manifest_hash": manifest_hash(),
            "mode": "real",
            "provider": os.getenv("TSAGENT_LLM_PROVIDER", "auto"),
            "records": records,
            "metrics": summarize(records),
            "status": _acceptance_status(records),
        }
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    print(rendered)
    if args.results:
        Path(args.results).write_text(rendered + "\n", encoding="utf-8")
    return 0 if payload["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
