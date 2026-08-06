"""离线预检 v2.2C Run 级恢复逻辑（fake executor，不调用真实 API）。

直接调用 restart_worker.phase_a / phase_resume / _activate_impl，用 fake
executor 替代 LLM/Tool，快速验证：index 激活、spec 完成、resume impl、
激活 verify、无重复副作用、A 不重跑。真实 API 冒烟前先跑本脚本。
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.environ["TRANSFORMERS_NO_TF"] = "1"
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"

from agent.executor.contract import executor_factory
from agent.workflow import ExecutionResult
from benchmarks.v22c import restart_worker


SPEC_TEXT = "# spec\n\n目标：solution.py 提供 square(n)。\n功能：square 返回 n 的平方。"
CODE_TEXT = (
    "def square(n):\n"
    "    return n * n\n\n"
    "def main():\n"
    "    import sys\n"
    "    n = int(sys.stdin.read().strip())\n"
    "    print(square(n))\n\n"
    "if __name__ == '__main__':\n"
    "    main()\n"
)
REPORT_TEXT = "# report\n\n结论：PASS。solution.py 实现 square(n)。"


class FakeLLM:
    async def execute(self, task, context):
        goal = task.goal or ""
        if "需求分析师" in goal or "spec.md" in goal:
            return ExecutionResult(success=True, outputs={"text": SPEC_TEXT})
        if "Python 工程师" in goal or "solution.py" in goal:
            return ExecutionResult(success=True, outputs={"text": CODE_TEXT})
        if "QA" in goal or "验证报告" in goal:
            return ExecutionResult(success=True, outputs={"text": REPORT_TEXT})
        return ExecutionResult(success=True, outputs={"text": "fallback"})


class FakeTool:
    async def execute(self, task, context):
        inputs = task.inputs or {}
        path = str(inputs.get("path", ""))
        content = str(inputs.get("content", ""))
        if path and content:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            return ExecutionResult(success=True, outputs={"text": f"written {path}"})
        if path:
            try:
                with open(path, encoding="utf-8") as f:
                    return ExecutionResult(success=True, outputs={"text": f.read()})
            except OSError as exc:
                return ExecutionResult(success=False, error=str(exc))
        return ExecutionResult(success=False, error="no path")


def _fresh() -> tuple[str, str]:
    workspace = tempfile.mkdtemp(prefix="v22c-offline-ws-")
    store_dir = tempfile.mkdtemp(prefix="v22c-offline-store-")
    return workspace, store_dir


def _check(case_name: str, checks: dict) -> tuple[bool, str]:
    lines = []
    ok = True
    for name, value in checks.items():
        lines.append(f"  {'PASS' if value else 'FAIL'}  {name}")
        ok = ok and bool(value)
    lines.append(f"{case_name}: {'PASS' if ok else 'FAIL'}")
    return ok, "\n".join(lines)


def _run_c02() -> tuple[bool, str]:
    workspace, store_dir = _fresh()
    try:
        ev_a = asyncio.run(restart_worker.phase_a(workspace, store_dir))
        ev_r = asyncio.run(restart_worker.phase_resume("c02", workspace, store_dir))
        checks = {
            "spec_success": ev_a["spec_success"],
            "spec completed in index": ev_a["index"]["completed"] == ["spec"],
            "a_checkpoint_loaded": ev_r["a_checkpoint_loaded"],
            "b_success": ev_r["b_success"],
            "c_success": ev_r["c_success"],
            "resume no spec workflow": "spec" not in ev_r["workflow_counts"],
            "spec_md_unchanged": ev_r["spec_md_unchanged"],
            "solution_exists": ev_r["solution_exists"],
            "report_exists": ev_r["report_exists"],
            "no duplicate side effects": not ev_r["duplicate_side_effects"],
            "revision_advanced": ev_r["revision_advanced"],
            "all completed after": ev_r["index"]["completed"] == ["spec", "impl", "verify"],
            "no active after": ev_r["index"]["active"] == "",
        }
        return _check("C02", checks)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
        shutil.rmtree(store_dir, ignore_errors=True)


def _run_c03() -> tuple[bool, str]:
    workspace, store_dir = _fresh()
    try:
        ev_a = asyncio.run(restart_worker.phase_a(workspace, store_dir))
        ev_b = asyncio.run(
            restart_worker._activate_impl(
                workspace, store_dir, interrupt_after_stage_id="read_spec"
            )
        )
        ev_r = asyncio.run(restart_worker.phase_resume("c03", workspace, store_dir))
        b_decision = ev_r.get("b_decision") or {}
        checks = {
            "spec_success": ev_a["spec_success"],
            "B 中断在 gen_code 前": ev_b.get("checkpoint_active_stage_id") == "gen_code",
            "read_spec 已提交": ev_b.get("checkpoint_completed_stage_ids") == ["read_spec"],
            "resume b_success": ev_r["b_success"],
            "resume c_success": ev_r["c_success"],
            "action=RESUME_EXACT": b_decision.get("workflow_action") == "RESUME_EXACT",
            "selected=impl": b_decision.get("selected_workflow_id") == "impl",
            "skipped=[spec]": b_decision.get("skipped_workflow_ids") == ["spec"],
            "read_spec 不重跑": ev_r.get("execution_counts", {}).get("impl", 0) == 2,
            "no duplicate side effects": not ev_r["duplicate_side_effects"],
            "all completed after": ev_r["index"]["completed"] == ["spec", "impl", "verify"],
            "no active after": ev_r["index"]["active"] == "",
        }
        return _check("C03", checks)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
        shutil.rmtree(store_dir, ignore_errors=True)


def _run_c07() -> tuple[bool, str]:
    from benchmarks.v22c.harness import SimulatedCrash

    workspace, store_dir = _fresh()
    try:
        ev_a = asyncio.run(restart_worker.phase_a(workspace, store_dir))
        crashed = False
        try:
            asyncio.run(
                restart_worker._activate_impl(
                    workspace, store_dir, crash_after_write_path="solution.py"
                )
            )
        except SimulatedCrash as exc:
            crashed = True
            print("  crash injected:", exc)
        solution_crash = restart_worker._sha(
            os.path.join(workspace, "output", "solution.py")
        )
        ev_r = asyncio.run(restart_worker.phase_resume("c07", workspace, store_dir))
        resume_ledger = ev_r.get("side_effects", {})
        impl_written_in_resume = any(
            key.endswith("/impl") and value > 0
            for key, value in resume_ledger.items()
        )
        checks = {
            "spec_success": ev_a["spec_success"],
            "crash 注入成功": crashed,
            "crash 前 solution.py 已落盘": bool(solution_crash),
            "resume b_success": ev_r["b_success"],
            "resume c_success": ev_r["c_success"],
            "B 未在 resume 重新写入(Duplicate=0)": not impl_written_in_resume,
            "B LLM 未再次调用": ev_r.get("llm_counts", {}).get("impl", 0) == 0,
        }
        return _check("C07", checks)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
        shutil.rmtree(store_dir, ignore_errors=True)


def _run_c04() -> tuple[bool, str]:
    workspace, store_dir = _fresh()
    try:
        ev_a = asyncio.run(restart_worker.phase_a(workspace, store_dir, impl_idempotent=True))
        ev_b = asyncio.run(
            restart_worker._activate_impl(
                workspace, store_dir, interrupt_after_stage_id="read_spec"
            )
        )
        ev_r = asyncio.run(restart_worker.phase_resume("c04", workspace, store_dir))
        b_decision = ev_r.get("b_decision") or {}
        checks = {
            "spec_success": ev_a["spec_success"],
            "active=gen_code": ev_b.get("checkpoint_active_stage_id") == "gen_code",
            "action=REPLAY_FROM_STAGE": b_decision.get("workflow_action") == "REPLAY_FROM_STAGE",
            "b_success": ev_r["b_success"],
            "c_success": ev_r["c_success"],
            "read_spec 不重跑(exec=2)": ev_r.get("execution_counts", {}).get("impl", 0) == 2,
            "gen_code 重放(LLM=1)": ev_r.get("llm_counts", {}).get("impl", 0) == 1,
            "write_code 1 次": ev_r.get("tool_counts", {}).get("impl", 0) == 1,
            "solution_exists": ev_r["solution_exists"],
            "report_exists": ev_r["report_exists"],
            "no duplicate side effects": not ev_r["duplicate_side_effects"],
            "all completed": ev_r.get("index", {}).get("completed") == ["spec", "impl", "verify"],
            "no active": ev_r.get("index", {}).get("active") == "",
        }
        return _check("C04", checks)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
        shutil.rmtree(store_dir, ignore_errors=True)


def _run_c05() -> tuple[bool, str]:
    from benchmarks.v22c.harness import SimulatedCrash

    workspace, store_dir = _fresh()
    try:
        ev_a = asyncio.run(restart_worker.phase_a(workspace, store_dir))
        ev_b = asyncio.run(restart_worker._run_impl_full(workspace, store_dir))
        crashed = False
        try:
            asyncio.run(restart_worker._crash_verify(workspace, store_dir))
        except SimulatedCrash as exc:
            crashed = True
            print("  crash injected (verify):", exc)
        ev_r = asyncio.run(restart_worker.phase_resume("c05", workspace, store_dir))
        second = ev_r.get("second_call_decision") or {}
        checks = {
            "spec_success": ev_a["spec_success"],
            "B full success": ev_b["b_success"],
            "B completed in index": ev_b.get("index", {}).get("completed") == ["spec", "impl"],
            "c_crash 注入成功": crashed,
            "c_success": ev_r["c_success"],
            "resume 中 A exec=0": ev_r.get("a_b_execution_in_resume", {}).get("spec") == 0,
            "resume 中 B exec=0": ev_r.get("a_b_execution_in_resume", {}).get("impl") == 0,
            "spec_unchanged": ev_r["spec_unchanged"],
            "solution_unchanged": ev_r["solution_unchanged"],
            "report_unchanged": ev_r["report_unchanged"],
            "second call NO_EXECUTION": ev_r["second_call_result"] == "NO_EXECUTION",
            "second call RUN_COMPLETED": second.get("reason_code") == "RUN_COMPLETED",
            "all completed": ev_r.get("index", {}).get("completed") == ["spec", "impl", "verify"],
            "no active": ev_r.get("index", {}).get("active") == "",
        }
        return _check("C05", checks)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
        shutil.rmtree(store_dir, ignore_errors=True)


def _run_c06() -> tuple[bool, str]:
    workspace, store_dir = _fresh()
    try:
        ev_a = asyncio.run(restart_worker.phase_a(workspace, store_dir))
        ev_req = asyncio.run(restart_worker.phase_req_b(workspace, store_dir))
        spec_path = os.path.join(workspace, "output", "spec.md")
        with open(spec_path, "a", encoding="utf-8") as f:
            f.write("\n[tampered by C06]\n")
        ev_r = asyncio.run(restart_worker.phase_resume("c06", workspace, store_dir))
        b_decision = ev_r.get("b_decision") or {}
        checks = {
            "spec_success": ev_a["spec_success"],
            "req_b 声明上游依赖": bool(ev_req.get("declared_artifact", {}).get("artifact_id")),
            "B 被阻断": ev_r["b_success"] is False,
            "disposition=REJECT": b_decision.get("disposition") == "REJECT",
            "reason=UPSTREAM_ARTIFACT_CHANGED": b_decision.get("reason_code") == "UPSTREAM_ARTIFACT_CHANGED",
            "B Provider=0": ev_r.get("llm_counts", {}).get("impl", 0) == 0,
            "B Tool=0": ev_r.get("tool_counts", {}).get("impl", 0) == 0,
            "solution 未生成": ev_r["solution_exists"] is False,
            "B 未标 completed": ev_r["impl_completed"] is False,
        }
        return _check("C06", checks)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
        shutil.rmtree(store_dir, ignore_errors=True)


def _run_c08() -> tuple[bool, str]:
    workspace, store_dir = _fresh()
    try:
        ev_a = asyncio.run(restart_worker.phase_a(workspace, store_dir))
        ev_b = asyncio.run(
            restart_worker._activate_impl(
                workspace, store_dir, interrupt_after_stage_id="read_spec"
            )
        )
        ev_r = asyncio.run(restart_worker.phase_resume("c08", workspace, store_dir))
        wr = ev_r.get("wrong_run_decision") or {}
        wf = ev_r.get("wrong_flow_decision") or {}
        checks = {
            "spec_success": ev_a["spec_success"],
            "wrong_run NO_EXECUTION": ev_r["wrong_run_result"] == "NO_EXECUTION",
            "wrong_flow NO_EXECUTION": ev_r["wrong_flow_result"] == "NO_EXECUTION",
            "wrong_run REJECT RUN_MISMATCH": (
                wr.get("disposition") == "REJECT" and wr.get("reason_code") == "RUN_MISMATCH"
            ),
            "wrong_flow REQUIRE_CLARIFICATION": wf.get("disposition") == "REQUIRE_CLARIFICATION",
            "no executor calls": ev_r.get("execution_counts") == {},
            "revision unchanged": ev_r["revision_unchanged"],
            "checkpoint count unchanged": ev_r.get("checkpoint_count_before") == ev_r.get("checkpoint_count_after"),
            "workspace unchanged": ev_r["workspace_unchanged"],
        }
        return _check("C08", checks)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
        shutil.rmtree(store_dir, ignore_errors=True)


def main() -> int:
    original = dict(executor_factory._registry)
    executor_factory._registry["llm"] = FakeLLM
    executor_factory._registry["tool"] = FakeTool
    try:
        ok = True
        for case_name, runner in (
            ("C02", _run_c02),
            ("C03", _run_c03),
            ("C04", _run_c04),
            ("C05", _run_c05),
            ("C06", _run_c06),
            ("C07", _run_c07),
            ("C08", _run_c08),
        ):
            print(f"\n========== {case_name} ==========")
            case_ok, detail = runner()
            print(detail)
            ok = ok and case_ok
        print("\nOFFLINE DRY RUN:", "PASS" if ok else "FAIL")
        return 0 if ok else 1
    finally:
        executor_factory._registry.clear()
        executor_factory._registry.update(original)


if __name__ == "__main__":
    sys.exit(main())
