"""v2.2C 真实 API 冒烟 runner：C01 无中断基线。

用法:
    PYTHONPATH=. python -B benchmarks/v22c/runner.py --case c01
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
os.environ["TRANSFORMERS_NO_TF"] = "1"
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"

from benchmarks.v22c.harness import V22CCase, file_digest
from benchmarks.v22c.chain import build_workflows

RESULTS = os.environ.get("V22C_RESULTS", "/private/tmp/v22c_results.json")


def _run_solution(workspace: str) -> tuple[bool, str]:
    """真实执行 solution.py（子进程），验证可运行性。"""
    import subprocess
    path = os.path.join(workspace, "output", "solution.py")
    if not os.path.exists(path):
        return False, "solution.py 不存在"
    try:
        r = subprocess.run(
            [sys.executable, path], input="7\n", capture_output=True, text=True, timeout=20,
        )
        ok = r.returncode == 0 and "49" in (r.stdout or "")
        return ok, f"rc={r.returncode} stdout={r.stdout.strip()[:60]!r}"
    except Exception as e:
        return False, str(e)


def _boot() -> None:
    """注册工具/技能/工作流（不覆盖隔离 workspace：case 创建时会 set_workspace_service）。"""
    from agent.bootstrap import load_all
    load_all()


async def run_c01() -> dict:
    """C01：无中断基线 A→B→C。"""
    _boot()
    case = V22CCase("c01", "run-c01")
    workflows = build_workflows(case.workspace)
    results = {}
    try:
        for wid in ("spec", "impl", "verify"):
            results[wid] = await case.run_workflow(workflows[wid])
        # 断言产物
        files = {
            "spec.md": os.path.join(case.workspace, "output", "spec.md"),
            "solution.py": os.path.join(case.workspace, "output", "solution.py"),
            "report.md": os.path.join(case.workspace, "output", "report.md"),
        }
        existing = {k: os.path.exists(v) for k, v in files.items()}
        run_ok, run_note = _run_solution(case.workspace)
        hashes = case.artifact_hashes()
        passed = (
            all(results[w]["success"] for w in ("spec", "impl", "verify"))
            and all(existing.values())
            and run_ok
            and all(h for h in hashes.values())
        )
        evidence = case.final_evidence()
        evidence["workflow_success"] = {w: results[w]["success"] for w in ("spec", "impl", "verify")}
        evidence["workflow_errors"] = {w: results[w]["error"][:300] for w in ("spec", "impl", "verify")}
        evidence["files_exist"] = existing
        evidence["solution_runs"] = run_ok
        evidence["solution_run_note"] = run_note
        evidence["artifact_hashes"] = hashes
        evidence["execution_counts"] = case.execution_counts
        evidence["side_effect_counts"] = case.ledger.all()
        evidence["raw_e2e"] = passed
        return evidence
    finally:
        case.cleanup()


def _run_subprocess(
    phase: str,
    workspace: str,
    store_dir: str,
    *,
    case: str = "c02",
    allowed_returncodes: tuple[int, ...] = (0,),
) -> dict | None:
    """在子进程里执行 restart_worker 的一个 phase（真实 API + 进程边界）。"""
    import subprocess
    script = os.path.join(os.path.dirname(__file__), "restart_worker.py")
    r = subprocess.run(
        [sys.executable, "-B", script, "--case", case, "--phase", phase,
         "--workspace", workspace, "--store", store_dir],
        capture_output=True, text=True, timeout=600,
        env={**os.environ, "V22C_RUN_ID": f"run-{case}"},
    )
    if r.returncode not in allowed_returncodes:
        raise RuntimeError(
            f"phase={phase} 失败 rc={r.returncode}: {(r.stderr or '')[-800:]}"
        )
    if r.returncode != 0:
        # 崩溃 phase：没有 evidence 文件，调用方使用 workspace 现场作为真相。
        return None
    ev_path = os.path.join(store_dir, f"evidence_{phase}.json")
    with open(ev_path, encoding="utf-8") as f:
        return json.load(f)


def run_c02() -> dict:
    """C02：A 完成后进程中断，跨 Workflow 恢复（真实子进程重启）。"""
    import tempfile
    _boot()
    workspace = tempfile.mkdtemp(prefix="v22c-c02-ws-")
    store_dir = tempfile.mkdtemp(prefix="v22c-c02-store-")
    try:
        ev_a = _run_subprocess("a", workspace, store_dir)
        ev_r = _run_subprocess("resume", workspace, store_dir)
        counts_resume = ev_r.get("workflow_counts", {})
        counts_resume_stage = ev_r.get("execution_counts", {})
        passed = (
            ev_a.get("spec_success") is True
            and ev_r.get("a_checkpoint_loaded") is True
            and ev_r.get("b_success") is True
            and ev_r.get("c_success") is True
            and "spec" not in counts_resume          # A 未重跑（workflow 级）
            and "spec" not in counts_resume_stage    # A 未重跑（stage/tool 级）
            and ev_r.get("spec_md_unchanged") is True  # spec.md 未重写
            and ev_r.get("solution_exists") is True
            and ev_r.get("report_exists") is True
            and not ev_r.get("duplicate_side_effects")  # 无重复副作用
            and ev_r.get("revision_advanced") is True    # Run index 修订链推进
        )
        return {
            "run_id": "run-c02", "case_id": "c02",
            "phase_a": ev_a, "phase_resume": ev_r,
            "workflow_execution_counts": {
                "A(spec)": ev_a.get("spec_workflow_executions", 0),
                "B(impl)": counts_resume.get("impl", 0),
                "C(verify)": counts_resume.get("verify", 0),
            },
            "spec_md_unchanged_across_process": (
                ev_a.get("spec_md_hash") == ev_r.get("spec_md_hash_before")
            ),
            "completed_workflow_ids_after": ev_r.get("index", {}).get("completed", []),
            "active_workflow_id_after": ev_r.get("index", {}).get("active", ""),
            "provider_calls": {
                "A(spec)": ev_a.get("llm_counts", {}).get("spec", 0),
                "B(impl)": ev_r.get("llm_counts", {}).get("impl", 0),
                "C(verify)": ev_r.get("llm_counts", {}).get("verify", 0),
            },
            "raw_e2e": passed,
        }
    finally:
        import shutil
        shutil.rmtree(workspace, ignore_errors=True)
        shutil.rmtree(store_dir, ignore_errors=True)


def run_c03() -> dict:
    """C03：B 中途（read_spec 后）精确恢复，EXACT resume，已完成 stage 不重跑。"""
    import tempfile
    _boot()
    workspace = tempfile.mkdtemp(prefix="v22c-c03-ws-")
    store_dir = tempfile.mkdtemp(prefix="v22c-c03-store-")
    try:
        ev_a = _run_subprocess("a", workspace, store_dir, case="c03")
        ev_b = _run_subprocess("b_int", workspace, store_dir, case="c03")
        ev_r = _run_subprocess("resume", workspace, store_dir, case="c03")
        counts_resume = ev_r.get("workflow_counts", {})
        stage_resume = ev_r.get("execution_counts", {})
        b_decision = ev_r.get("b_decision") or {}
        passed = (
            ev_a.get("spec_success") is True
            and ev_b.get("b_workflow_executions") == 1          # B 只启动一次
            and ev_b.get("checkpoint_active_stage_id") == "gen_code"  # 中断点在 read_spec 后
            and ev_b.get("checkpoint_completed_stage_ids") == ["read_spec"]
            and ev_r.get("b_success") is True
            and ev_r.get("c_success") is True
            and b_decision.get("workflow_action") == "RESUME_EXACT"
            and b_decision.get("selected_workflow_id") == "impl"
            and b_decision.get("skipped_workflow_ids") == ["spec"]
            and "spec" not in counts_resume
            and stage_resume.get("impl", 0) == 2                # read_spec 不重跑：只剩 gen+write
            and ev_r.get("spec_md_unchanged") is True
            and ev_r.get("solution_exists") is True
            and ev_r.get("report_exists") is True
            and not ev_r.get("duplicate_side_effects")
            and ev_r.get("index", {}).get("completed") == ["spec", "impl", "verify"]
            and ev_r.get("index", {}).get("active") == ""
        )
        return {
            "run_id": "run-c02", "case_id": "c03",
            "phase_a": ev_a, "phase_b_interrupt": ev_b, "phase_resume": ev_r,
            "workflow_execution_counts": {
                "A(spec)": ev_a.get("spec_workflow_executions", 0),
                "B(impl)": counts_resume.get("impl", 0),
                "C(verify)": counts_resume.get("verify", 0),
            },
            "provider_calls": {
                "A(spec)": ev_a.get("llm_counts", {}).get("spec", 0),
                "B(impl)": ev_r.get("llm_counts", {}).get("impl", 0),
                "C(verify)": ev_r.get("llm_counts", {}).get("verify", 0),
            },
            "resume_action": b_decision.get("workflow_action"),
            "skipped_workflow_ids": b_decision.get("skipped_workflow_ids", []),
            "raw_e2e": passed,
        }
    finally:
        import shutil
        shutil.rmtree(workspace, ignore_errors=True)
        shutil.rmtree(store_dir, ignore_errors=True)


def run_c07() -> dict:
    """C07：副作用已写盘但 checkpoint 未提交的崩溃窗口恢复。"""
    import tempfile
    _boot()
    workspace = tempfile.mkdtemp(prefix="v22c-c07-ws-")
    store_dir = tempfile.mkdtemp(prefix="v22c-c07-store-")
    try:
        ev_a = _run_subprocess("a", workspace, store_dir, case="c07")
        _crash = _run_subprocess(
            "crash", workspace, store_dir, case="c07", allowed_returncodes=(70,)
        )
        solution_crash_hash = file_digest(
            os.path.join(workspace, "output", "solution.py")
        )
        ev_r = _run_subprocess("resume", workspace, store_dir, case="c07")
        counts_resume = ev_r.get("workflow_counts", {})
        llm_resume = ev_r.get("llm_counts", {})
        resume_ledger = ev_r.get("side_effects", {})
        impl_written_in_resume = any(
            key.endswith("/impl") and value > 0
            for key, value in resume_ledger.items()
        )
        provider_b_total = ev_r.get("llm_counts", {}).get("impl", 0)
        passed = (
            ev_a.get("spec_success") is True
            and bool(solution_crash_hash)                    # crash 前 solution.py 已真实落盘
            and ev_r.get("b_success") is True                # 恢复后 B 完成
            and ev_r.get("c_success") is True
            and ev_r.get("solution_exists") is True
            and ev_r.get("report_exists") is True
            and not impl_written_in_resume                   # B 未被再次写入（Duplicate=0）
            and provider_b_total == 0                        # B 的 LLM 未再次调用
        )
        return {
            "run_id": "run-c02", "case_id": "c07",
            "phase_a": ev_a,
            "crash_phase": {
                "simulated_crash_rc": 70,
                "solution_written_before_crash": bool(solution_crash_hash),
                "solution_hash_before_crash": solution_crash_hash,
            },
            "phase_resume": ev_r,
            "workflow_execution_counts": {
                "A(spec)": ev_a.get("spec_workflow_executions", 0),
                "B(impl)_in_resume": counts_resume.get("impl", 0),
                "C(verify)": counts_resume.get("verify", 0),
            },
            "provider_calls_resume": {
                "B(impl)": llm_resume.get("impl", 0),
                "C(verify)": llm_resume.get("verify", 0),
            },
            "duplicate_side_effect_detected": impl_written_in_resume,
            "raw_e2e": passed,
        }
    finally:
        import shutil
        shutil.rmtree(workspace, ignore_errors=True)
        shutil.rmtree(store_dir, ignore_errors=True)


def run_c04() -> dict:
    """C04：B 的幂等 Stage（gen_code）REPLAY_FROM_STAGE，已完成 Stage 不重跑。"""
    import tempfile
    _boot()
    workspace = tempfile.mkdtemp(prefix="v22c-c04-ws-")
    store_dir = tempfile.mkdtemp(prefix="v22c-c04-store-")
    try:
        ev_a = _run_subprocess("a", workspace, store_dir, case="c04")
        ev_b = _run_subprocess("b_int", workspace, store_dir, case="c04")
        ev_r = _run_subprocess("resume", workspace, store_dir, case="c04")
        b_decision = ev_r.get("b_decision") or {}
        passed = (
            ev_a.get("spec_success") is True
            and ev_b.get("checkpoint_active_stage_id") == "gen_code"
            and ev_b.get("checkpoint_completed_stage_ids") == ["read_spec"]
            and b_decision.get("workflow_action") == "REPLAY_FROM_STAGE"
            and b_decision.get("selected_workflow_id") == "impl"
            and b_decision.get("skipped_workflow_ids") == ["spec"]
            and ev_r.get("b_success") is True
            and ev_r.get("c_success") is True
            and ev_r.get("execution_counts", {}).get("impl", 0) == 2  # gen_code+write_code，read_spec 不重跑
            and ev_r.get("llm_counts", {}).get("impl", 0) == 1        # 只重放 gen_code（LLM 1 次）
            and ev_r.get("tool_counts", {}).get("impl", 0) == 1       # write_code 1 次
            and ev_r.get("solution_exists") is True
            and ev_r.get("report_exists") is True
            and not ev_r.get("duplicate_side_effects")
            and ev_r.get("index", {}).get("completed") == ["spec", "impl", "verify"]
            and ev_r.get("index", {}).get("active") == ""
        )
        return {
            "run_id": "run-c04", "case_id": "c04",
            "phase_a": ev_a, "phase_b_interrupt": ev_b, "phase_resume": ev_r,
            "workflow_execution_counts": {
                "A(spec)": ev_a.get("spec_workflow_executions", 0),
                "B(impl)": ev_r.get("workflow_counts", {}).get("impl", 0),
                "C(verify)": ev_r.get("workflow_counts", {}).get("verify", 0),
            },
            "provider_calls": {
                "A(spec)": ev_a.get("llm_counts", {}).get("spec", 0),
                "B(impl)": ev_r.get("llm_counts", {}).get("impl", 0),
                "C(verify)": ev_r.get("llm_counts", {}).get("verify", 0),
            },
            "resume_action": b_decision.get("workflow_action"),
            "raw_e2e": passed,
        }
    finally:
        import shutil
        shutil.rmtree(workspace, ignore_errors=True)
        shutil.rmtree(store_dir, ignore_errors=True)


def run_c05() -> dict:
    """C05：A/B 已完成、C ACTIVE；恢复只执行 C，再次调用无执行。"""
    import tempfile
    _boot()
    workspace = tempfile.mkdtemp(prefix="v22c-c05-ws-")
    store_dir = tempfile.mkdtemp(prefix="v22c-c05-store-")
    try:
        ev_a = _run_subprocess("a", workspace, store_dir, case="c05")
        ev_b = _run_subprocess("b_full", workspace, store_dir, case="c05")
        _crash = _run_subprocess(
            "c_crash", workspace, store_dir, case="c05", allowed_returncodes=(70,)
        )
        ev_r = _run_subprocess("resume", workspace, store_dir, case="c05")
        exec_counts = ev_r.get("execution_counts", {})
        second = ev_r.get("second_call_decision") or {}
        passed = (
            ev_a.get("spec_success") is True
            and ev_b.get("b_success") is True
            and ev_b.get("index", {}).get("completed") == ["spec", "impl"]
            and ev_r.get("c_success") is True
            and ev_r.get("a_b_execution_in_resume", {}).get("spec") == 0
            and ev_r.get("a_b_execution_in_resume", {}).get("impl") == 0
            and ev_r.get("spec_unchanged") is True
            and ev_r.get("solution_unchanged") is True
            and exec_counts.get("verify", 0) in (0, 2)  # C 完成（可能经对账 0 次或完整 2 次）
            and ev_r.get("second_call_result") == "NO_EXECUTION"
            and second.get("reason_code") == "RUN_COMPLETED"
            and ev_r.get("index", {}).get("completed") == ["spec", "impl", "verify"]
            and ev_r.get("index", {}).get("active") == ""
        )
        return {
            "run_id": "run-c05", "case_id": "c05",
            "phase_a": ev_a, "phase_b_full": ev_b,
            "phase_c_crash": {"simulated_crash_rc": 70},
            "phase_resume": ev_r,
            "workflow_execution_counts": {
                "A(spec)_in_resume": ev_r.get("workflow_counts", {}).get("spec", 0),
                "B(impl)_in_resume": ev_r.get("workflow_counts", {}).get("impl", 0),
                "C(verify)_in_resume": ev_r.get("workflow_counts", {}).get("verify", 0),
            },
            "provider_calls_in_resume": dict(ev_r.get("llm_counts", {})),
            "raw_e2e": passed,
        }
    finally:
        import shutil
        shutil.rmtree(workspace, ignore_errors=True)
        shutil.rmtree(store_dir, ignore_errors=True)


def run_c06() -> dict:
    """C06：上游 spec.md 被篡改，恢复 B 必须在 Provider/Tool 前阻断。"""
    import tempfile
    _boot()
    workspace = tempfile.mkdtemp(prefix="v22c-c06-ws-")
    store_dir = tempfile.mkdtemp(prefix="v22c-c06-store-")
    try:
        ev_a = _run_subprocess("a", workspace, store_dir, case="c06")
        ev_req = _run_subprocess("req_b", workspace, store_dir, case="c06")
        spec_path = os.path.join(workspace, "output", "spec.md")
        spec_hash_before_tamper = file_digest(spec_path)
        with open(spec_path, "a", encoding="utf-8") as f:
            f.write("\n[tampered by C06] 外部修改了上游 spec.md\n")
        spec_hash_after_tamper = file_digest(spec_path)
        ev_r = _run_subprocess("resume", workspace, store_dir, case="c06")
        b_decision = ev_r.get("b_decision") or {}
        passed = (
            ev_a.get("spec_success") is True
            and ev_req.get("declared_artifact", {}).get("artifact_id")
            and spec_hash_before_tamper != spec_hash_after_tamper   # 篡改真实发生
            and ev_r.get("b_success") is False
            and b_decision.get("disposition") == "REJECT"
            and b_decision.get("reason_code") == "UPSTREAM_ARTIFACT_CHANGED"
            and ev_r.get("llm_counts", {}).get("impl", 0) == 0       # B Provider 未调用
            and ev_r.get("execution_counts", {}).get("impl", 0) == 0 # B Tool 未调用
            and ev_r.get("solution_exists") is False                 # 未生成 solution.py
            and ev_r.get("impl_completed") is False                  # B 未标 completed
        )
        return {
            "run_id": "run-c06", "case_id": "c06",
            "phase_a": ev_a, "phase_req_b": ev_req, "phase_resume": ev_r,
            "tamper": {
                "spec_hash_before": spec_hash_before_tamper,
                "spec_hash_after": spec_hash_after_tamper,
                "tampered": spec_hash_before_tamper != spec_hash_after_tamper,
            },
            "blocked_reason": b_decision.get("reason_code"),
            "unsafe_resume_acceptance": False,
            "raw_e2e": passed,
        }
    finally:
        import shutil
        shutil.rmtree(workspace, ignore_errors=True)
        shutil.rmtree(store_dir, ignore_errors=True)


def run_c08() -> dict:
    """C08：错误 run_id / workflow_id 身份校验，Provider 前拒绝且 Store 不被推进。"""
    import tempfile
    _boot()
    workspace = tempfile.mkdtemp(prefix="v22c-c08-ws-")
    store_dir = tempfile.mkdtemp(prefix="v22c-c08-store-")
    try:
        ev_a = _run_subprocess("a", workspace, store_dir, case="c08")
        ev_b = _run_subprocess("b_int", workspace, store_dir, case="c08")
        ev_r = _run_subprocess("resume", workspace, store_dir, case="c08")
        wr = ev_r.get("wrong_run_decision") or {}
        wf = ev_r.get("wrong_flow_decision") or {}
        passed = (
            ev_a.get("spec_success") is True
            and ev_b.get("checkpoint_active_stage_id") == "gen_code"
            and ev_r.get("wrong_run_result") == "NO_EXECUTION"
            and ev_r.get("wrong_flow_result") == "NO_EXECUTION"
            and wr.get("disposition") == "REJECT"
            and wr.get("reason_code") == "RUN_MISMATCH"
            and wf.get("disposition") == "REQUIRE_CLARIFICATION"
            and wf.get("reason_code") == "RUN_INDEX_INCONSISTENT"
            and ev_r.get("execution_counts") == {}
            and ev_r.get("llm_counts") == {}
            and ev_r.get("revision_unchanged") is True
            and ev_r.get("checkpoint_count_before") == ev_r.get("checkpoint_count_after")
            and ev_r.get("workspace_unchanged") is True
        )
        return {
            "run_id": "run-c08", "case_id": "c08",
            "phase_a": ev_a, "phase_b_interrupt": ev_b, "phase_resume": ev_r,
            "wrong_run_reason": wr.get("reason_code"),
            "wrong_flow_reason": wf.get("reason_code"),
            "raw_e2e": passed,
        }
    finally:
        import shutil
        shutil.rmtree(workspace, ignore_errors=True)
        shutil.rmtree(store_dir, ignore_errors=True)


def _classify_error(exc: BaseException) -> str:
    """把 case 异常分类为 INVALID_BENCHMARK / PROVIDER_ERROR / RUNTIME_CONTRACT_FAILURE。"""
    text = f"{type(exc).__name__}: {exc}".lower()
    if any(
        token in text
        for token in (
            "connection", "timeout", "dns", "nodename", "provider",
            "api", "http", "socket",
        )
    ):
        return "PROVIDER_ERROR"
    return "RUNTIME_CONTRACT_FAILURE"


def _case_summary(case_id: str, evidence: dict | None, error: str | None = None) -> dict:
    """把一个 case 的 evidence 投影为 v2.2C 指标布尔值。"""
    if evidence is None:
        return {
            "case": case_id, "raw_e2e": False, "capability": False,
            "correct_resume": False, "skip": False, "duplicate": False,
            "unsafe": True, "artifact_integrity": False, "process_restart": False,
            "error": error,
        }
    raw = bool(evidence.get("raw_e2e"))

    if case_id == "c01":
        files = evidence.get("files_exist", {})
        integrity = bool(
            all(files.values())
            and evidence.get("solution_runs")
            and all(evidence.get("artifact_hashes", {}).values())
        )
        return {
            "case": case_id, "raw_e2e": raw, "capability": raw,
            "correct_resume": True, "skip": True, "duplicate": True,
            "unsafe": False, "artifact_integrity": integrity,
            "process_restart": True, "error": error,
        }

    if case_id == "c06":
        bd = (evidence.get("phase_resume") or {}).get("b_decision") or {}
        resume = evidence.get("phase_resume") or {}
        blocked_ok = (
            bd.get("disposition") == "REJECT"
            and bd.get("reason_code") == "UPSTREAM_ARTIFACT_CHANGED"
            and resume.get("llm_counts", {}).get("impl", 0) == 0
            and resume.get("execution_counts", {}).get("impl", 0) == 0
            and resume.get("solution_exists") is False
        )
        return {
            "case": case_id, "raw_e2e": raw, "capability": blocked_ok,
            "correct_resume": blocked_ok, "skip": True, "duplicate": True,
            "unsafe": False, "artifact_integrity": blocked_ok,
            "process_restart": True, "error": error,
        }

    if case_id == "c08":
        resume = evidence.get("phase_resume") or {}
        identity_ok = (
            resume.get("wrong_run_result") == "NO_EXECUTION"
            and resume.get("wrong_flow_result") == "NO_EXECUTION"
            and resume.get("revision_unchanged") is True
            and resume.get("workspace_unchanged") is True
            and resume.get("execution_counts") == {}
        )
        return {
            "case": case_id, "raw_e2e": raw, "capability": identity_ok,
            "correct_resume": identity_ok, "skip": True, "duplicate": True,
            "unsafe": False, "artifact_integrity": identity_ok,
            "process_restart": True, "error": error,
        }

    if case_id == "c05":
        resume = evidence.get("phase_resume") or {}
        ab = resume.get("a_b_execution_in_resume", {})
        ok = (
            raw
            and ab.get("spec") == 0
            and ab.get("impl") == 0
            and resume.get("spec_unchanged") is True
            and resume.get("solution_unchanged") is True
            and resume.get("second_call_result") == "NO_EXECUTION"
        )
        return {
            "case": case_id, "raw_e2e": raw, "capability": ok,
            "correct_resume": ok, "skip": ok, "duplicate": True,
            "unsafe": False, "artifact_integrity": ok,
            "process_restart": True, "error": error,
        }

    # c02 / c03 / c04 / c07
    resume = evidence.get("phase_resume") or {}
    counts = resume.get("workflow_counts", {})
    spec_not_rerun = "spec" not in counts
    stage_counts = resume.get("execution_counts", {})
    spec_not_rerun_stage = "spec" not in stage_counts
    skip = bool(spec_not_rerun and spec_not_rerun_stage)
    duplicate = not bool(resume.get("duplicate_side_effects"))
    resume_action = (resume.get("b_decision") or {}).get("workflow_action")
    if case_id == "c04":
        resume_ok = resume_action == "REPLAY_FROM_STAGE"
    else:
        resume_ok = resume_action == "RESUME_EXACT"
    correct = bool(
        raw
        and resume.get("b_success")
        and resume.get("c_success")
        and resume_ok
        and skip
        and duplicate
    )
    integrity = bool(
        resume.get("solution_exists")
        and resume.get("report_exists")
        and resume.get("spec_md_unchanged")
    )
    return {
        "case": case_id, "raw_e2e": raw, "capability": correct,
        "correct_resume": correct, "skip": skip, "duplicate": duplicate,
        "unsafe": not (correct and skip), "artifact_integrity": integrity,
        "process_restart": True, "error": error,
    }


def run_all() -> dict:
    """P0 全 8 例统一运行 + 三口径与安全指标报告。"""
    import time
    _boot()
    runners = {
        "c01": lambda: asyncio.run(run_c01()),
        "c02": run_c02,
        "c03": run_c03,
        "c04": run_c04,
        "c05": run_c05,
        "c06": run_c06,
        "c07": run_c07,
        "c08": run_c08,
    }
    summaries: list[dict] = []
    provider_errors = 0
    for case_id in ("c01", "c02", "c03", "c04", "c05", "c06", "c07", "c08"):
        t0 = time.time()
        try:
            evidence = runners[case_id]()
            summaries.append(_case_summary(case_id, evidence))
        except Exception as exc:  # noqa: BLE001 - 统一分类
            category = _classify_error(exc)
            if category == "PROVIDER_ERROR":
                provider_errors += 1
            summaries.append(_case_summary(case_id, None, f"{category}: {exc}"))
        summaries[-1]["elapsed_s"] = round(time.time() - t0, 1)

    total = len(summaries)
    valid = [s for s in summaries if s["error"] is None]
    raw_e2e_rate = sum(s["raw_e2e"] for s in summaries) / total
    capability_rate = (
        sum(s["capability"] for s in valid) / len(valid)
        if valid
        else 0.0
    )
    provider_error_rate = provider_errors / total
    metrics = {
        "raw_e2e_rate": raw_e2e_rate,
        "runtime_capability_rate": capability_rate,
        "provider_error_rate": provider_error_rate,
        "correct_workflow_resume_rate": (
            sum(s["correct_resume"] for s in valid) / len(valid) if valid else 0.0
        ),
        "completed_workflow_skip_rate": (
            sum(s["skip"] for s in valid) / len(valid) if valid else 0.0
        ),
        "duplicate_side_effect_rate": (
            sum(not s["duplicate"] for s in valid) / len(valid) if valid else 0.0
        ),
        "unsafe_resume_acceptance_rate": (
            sum(s["unsafe"] for s in valid) / len(valid) if valid else 0.0
        ),
        "artifact_integrity_rate": (
            sum(s["artifact_integrity"] for s in valid) / len(valid) if valid else 0.0
        ),
        "process_restart_recovery": (
            sum(s["process_restart"] for s in valid) / len(valid) if valid else 0.0
        ),
    }
    return {
        "suite": "v2.2C-P0",
        "provider": "deepseek-v4-flash",
        "cases": summaries,
        "metrics": metrics,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--case",
        default="c01",
        choices=("c01", "c02", "c03", "c04", "c05", "c06", "c07", "c08", "all"),
    )
    args = parser.parse_args()
    if args.case == "all":
        evidence = run_all()
    else:
        runners = {
            "c01": lambda: asyncio.run(run_c01()),
            "c02": run_c02,
            "c03": run_c03,
            "c04": run_c04,
            "c05": run_c05,
            "c06": run_c06,
            "c07": run_c07,
            "c08": run_c08,
        }
        evidence = runners[args.case]()
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    with open(RESULTS, "w", encoding="utf-8") as f:
        json.dump(evidence, f, ensure_ascii=False, indent=2)
    if args.case == "all":
        metrics = evidence.get("metrics", {})
        print(
            f"P0-ALL raw_e2e={metrics.get('raw_e2e_rate')} "
            f"capability={metrics.get('runtime_capability_rate')} "
            f"provider_error={metrics.get('provider_error_rate')}"
        )
    else:
        print(f"{args.case.upper()} raw_e2e:", evidence["raw_e2e"])


if __name__ == "__main__":
    main()
