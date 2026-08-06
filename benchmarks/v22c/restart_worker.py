"""v2.2C 子进程 worker：Run 级跨 Workflow 恢复（真实进程重启 + 故障注入）。

case 拓扑（A→B→C 固定顺序，真实 LLM + 真实文件副作用）：
    A spec    需求分析 → output/spec.md
    B impl    代码实现 → output/solution.py
    C verify  验证报告 → output/report.md

phase（所有 case 共享 store/workspace，跨进程恢复）：
    a        执行 A 并持久化 Run index + checkpoint store
    b_int    [c03/c04] 激活 B，在 read_spec 后确定性中断（SUSPEND，EXACT/REPLAY 恢复点）
    b_full   [c05] 激活 B 并完整执行到 COMPLETED
    crash    [c07] 激活 B，真实写出 solution.py 后在记录 checkpoint 前崩溃（进程退出非零）
    c_crash  [c05] 激活 C，真实写出 report.md 后在记录 checkpoint 前崩溃（进程退出非零）
    req_b    [c06] 在 A 完成后，把 B 的上游 Artifact 依赖声明进 Run index
    resume   按 case 恢复：c02/03/07 恢复 B→C；c04 REPLAY B；c05 恢复 C；c06 期望阻断；c08 身份校验

用法:
    python -B benchmarks/v22c/restart_worker.py --case c02 --phase a \
        --workspace <dir> --store <dir>
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import replace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
os.environ["TRANSFORMERS_NO_TF"] = "1"
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"

from agent.checkpoint import ResumeAction
from agent.run_resume import (
    ArtifactRequirement,
    JsonRunResumeStore,
    RunResumeCoordinator,
    RunResumeIndex,
    RunResumeRequest,
    RunWorkflowStatus,
    WorkflowDependency,
    WorkflowSummary,
    run_index_digest,
)
from benchmarks.v22c.chain import build_workflows
from benchmarks.v22c.harness import (
    CountingWorkflowExecutor,
    SimulatedCrash,
    V22CCase,
    file_digest,
    snapshot_files,
)
from benchmarks.v22c.store import JsonCheckpointStore

RUN_ID = os.environ.get("V22C_RUN_ID", "run-c02")


def _sha(path: str) -> str:
    return file_digest(path)


def _initial_index(*, impl_idempotent: bool = False) -> RunResumeIndex:
    """三 Workflow 全部 PENDING 的初始 Run index（A→B→C 固定顺序）。"""
    return RunResumeIndex(
        run_id=RUN_ID,
        workflow_sequence=("spec", "impl", "verify"),
        workflows=(
            WorkflowSummary(
                workflow_id="spec",
                workflow_version="1.0.0",
                status=RunWorkflowStatus.PENDING,
            ),
            WorkflowSummary(
                workflow_id="impl",
                workflow_version="1.0.0",
                status=RunWorkflowStatus.PENDING,
                depends_on=("spec",),
                active_stage_idempotent=impl_idempotent,
            ),
            WorkflowSummary(
                workflow_id="verify",
                workflow_version="1.0.0",
                status=RunWorkflowStatus.PENDING,
                depends_on=("impl",),
            ),
        ),
        completed_workflow_ids=(),
        active_workflow_id="",
        active_checkpoint_id="",
        pending_workflow_ids=("spec", "impl", "verify"),
        workflow_dependencies=(
            WorkflowDependency("spec"),
            WorkflowDependency("impl", ("spec",)),
            WorkflowDependency("verify", ("impl",)),
        ),
        created_at="2026-08-06T00:00:00Z",
        updated_at="2026-08-06T00:00:00Z",
    )


def _coordinator(
    case: V22CCase,
    checkpoint_store,
    run_store,
    workflows,
    *,
    crash_after_write_path: str | None = None,
    interrupt_workflow_id: str | None = None,
    interrupt_after_stage_id: str | None = None,
):
    workflow_counts: dict[str, int] = {}
    coordinator = RunResumeCoordinator(
        run_store=run_store,
        checkpoint_store=checkpoint_store,
        workflows=workflows,
        workflow_executor=CountingWorkflowExecutor(
            workflow_counts,
            case.execution_counts,
            llm_counts=case.llm_counts,
            tool_counts=case.tool_counts,
            crash_after_write_path=crash_after_write_path,
            interrupt_workflow_id=interrupt_workflow_id,
            interrupt_after_stage_id=interrupt_after_stage_id,
        ),
    )
    return coordinator, workflow_counts


def _result_error(result) -> str:
    if result is None:
        return ""
    return (result.error[:300] if not result.success else "")


def _result_success(result) -> bool:
    return result is not None and result.success


def _decision_dict(decision) -> dict | None:
    return decision.to_dict() if decision is not None else None


def _index_state(index: RunResumeIndex | None) -> dict:
    if index is None:
        return {"revision": None, "completed": [], "active": "", "active_cp": ""}
    return {
        "revision": index.revision,
        "completed": list(index.completed_workflow_ids),
        "active": index.active_workflow_id,
        "active_cp": index.active_checkpoint_id,
    }


async def phase_a(workspace: str, store_dir: str, *, impl_idempotent: bool = False) -> dict:
    """Phase 1：执行 Workflow A（spec），持久化 Run index，然后进程退出。"""
    case = V22CCase("c02-a", RUN_ID, workspace=workspace)
    checkpoint_store = JsonCheckpointStore(os.path.join(store_dir, "checkpoints.json"))
    run_store = JsonRunResumeStore(os.path.join(store_dir, "run-resume.json"))
    workflows = build_workflows(case.workspace)
    run_store.save(_initial_index(impl_idempotent=impl_idempotent))
    coordinator, workflow_counts = _coordinator(
        case, checkpoint_store, run_store, workflows
    )

    execution = await case.run_coordinator(
        coordinator, RUN_ID, attempt_id="attempt-spec"
    )
    result = execution.execution_result
    index = run_store.get(RUN_ID)
    return {
        "run_id": RUN_ID,
        "phase": "a",
        "spec_success": _result_success(result),
        "spec_error": _result_error(result),
        "spec_workflow_executions": workflow_counts.get("spec", 0),
        "workflow_counts": dict(workflow_counts),
        "execution_counts": dict(case.execution_counts),
        "llm_counts": dict(case.llm_counts),
        "tool_counts": dict(case.tool_counts),
        "spec_md_hash": _sha(os.path.join(case.workspace, "output", "spec.md")),
        "index": _index_state(index),
        "side_effects": case.ledger.all(),
    }


async def _activate_impl(
    workspace: str,
    store_dir: str,
    *,
    crash_after_write_path: str | None = None,
    interrupt_after_stage_id: str | None = None,
) -> dict:
    """激活 B（impl）并执行到故障点（中断或崩溃）。"""
    case = V22CCase("c02-b", RUN_ID, workspace=workspace)
    checkpoint_store = JsonCheckpointStore(os.path.join(store_dir, "checkpoints.json"))
    run_store = JsonRunResumeStore(os.path.join(store_dir, "run-resume.json"))
    workflows = build_workflows(case.workspace)
    coordinator, workflow_counts = _coordinator(
        case,
        checkpoint_store,
        run_store,
        workflows,
        crash_after_write_path=crash_after_write_path,
        interrupt_workflow_id="impl",
        interrupt_after_stage_id=interrupt_after_stage_id,
    )
    execution = await case.run_coordinator(
        coordinator, RUN_ID, attempt_id="attempt-impl"
    )
    result = execution.execution_result
    index = run_store.get(RUN_ID)
    latest_cp = checkpoint_store.latest_for_workflow(RUN_ID, "impl")
    return {
        "run_id": RUN_ID,
        "phase": "b",
        "b_success": _result_success(result),
        "b_error": _result_error(result),
        "b_decision": _decision_dict(execution.decision),
        "b_workflow_executions": workflow_counts.get("impl", 0),
        "workflow_counts": dict(workflow_counts),
        "execution_counts": dict(case.execution_counts),
        "llm_counts": dict(case.llm_counts),
        "tool_counts": dict(case.tool_counts),
        "solution_md5": _sha(os.path.join(case.workspace, "output", "solution.py")),
        "index": _index_state(index),
        "checkpoint_status": (
            latest_cp.status.value if latest_cp is not None else None
        ),
        "checkpoint_active_stage_id": (
            latest_cp.active_stage_id if latest_cp is not None else None
        ),
        "checkpoint_completed_stage_ids": (
            list(latest_cp.completed_stage_ids)
            if latest_cp is not None
            else []
        ),
        "side_effects": case.ledger.all(),
    }


async def _run_impl_full(workspace: str, store_dir: str) -> dict:
    """[c05] 激活 B（impl）并完整执行到 COMPLETED。"""
    case = V22CCase("c05-b", RUN_ID, workspace=workspace)
    checkpoint_store = JsonCheckpointStore(os.path.join(store_dir, "checkpoints.json"))
    run_store = JsonRunResumeStore(os.path.join(store_dir, "run-resume.json"))
    workflows = build_workflows(case.workspace)
    coordinator, workflow_counts = _coordinator(
        case, checkpoint_store, run_store, workflows
    )
    execution = await case.run_coordinator(
        coordinator, RUN_ID, attempt_id="attempt-impl"
    )
    result = execution.execution_result
    index = run_store.get(RUN_ID)
    return {
        "run_id": RUN_ID,
        "phase": "b_full",
        "b_success": _result_success(result),
        "b_error": _result_error(result),
        "workflow_counts": dict(workflow_counts),
        "execution_counts": dict(case.execution_counts),
        "llm_counts": dict(case.llm_counts),
        "tool_counts": dict(case.tool_counts),
        "solution_md5": _sha(os.path.join(case.workspace, "output", "solution.py")),
        "index": _index_state(index),
        "side_effects": case.ledger.all(),
    }


async def _crash_verify(workspace: str, store_dir: str) -> dict:
    """[c05] 激活 C（verify），真实写出 report.md 后在记录 checkpoint 前崩溃。"""
    case = V22CCase("c05-c", RUN_ID, workspace=workspace)
    checkpoint_store = JsonCheckpointStore(os.path.join(store_dir, "checkpoints.json"))
    run_store = JsonRunResumeStore(os.path.join(store_dir, "run-resume.json"))
    workflows = build_workflows(case.workspace)
    coordinator, workflow_counts = _coordinator(
        case,
        checkpoint_store,
        run_store,
        workflows,
        crash_after_write_path="report.md",
        interrupt_workflow_id="verify",
        interrupt_after_stage_id=None,
    )
    execution = await case.run_coordinator(
        coordinator, RUN_ID, attempt_id="attempt-verify"
    )
    result = execution.execution_result
    index = run_store.get(RUN_ID)
    latest_cp = checkpoint_store.latest_for_workflow(RUN_ID, "verify")
    return {
        "run_id": RUN_ID,
        "phase": "c_crash",
        "c_success": _result_success(result),
        "c_error": _result_error(result),
        "workflow_counts": dict(workflow_counts),
        "execution_counts": dict(case.execution_counts),
        "llm_counts": dict(case.llm_counts),
        "tool_counts": dict(case.tool_counts),
        "report_md5": _sha(os.path.join(case.workspace, "output", "report.md")),
        "index": _index_state(index),
        "checkpoint_active_stage_id": (
            latest_cp.active_stage_id if latest_cp is not None else None
        ),
        "checkpoint_completed_stage_ids": (
            list(latest_cp.completed_stage_ids)
            if latest_cp is not None
            else []
        ),
        "side_effects": case.ledger.all(),
    }


async def phase_req_b(workspace: str, store_dir: str) -> dict:
    """[c06] A 完成后，把 B 的上游 Artifact 依赖（spec_file digest）声明进 Run index。"""
    run_store = JsonRunResumeStore(os.path.join(store_dir, "run-resume.json"))
    index = run_store.get(RUN_ID)
    if index is None:
        raise RuntimeError("Run index 不存在")
    spec_fact = next(
        (
            item
            for item in index.artifacts
            if item.artifact_type == "spec_file"
        ),
        None,
    )
    if spec_fact is None:
        raise RuntimeError("A 未发布 spec_file Artifact，无法声明上游依赖")
    impl = index.workflow("impl")
    if impl is None:
        raise RuntimeError("impl 不存在")
    declared = replace(
        impl,
        required_artifacts=(
            ArtifactRequirement(
                artifact_id=spec_fact.artifact_id,
                expected_digest=spec_fact.digest,
            ),
        ),
    )
    workflows = tuple(
        declared if item.workflow_id == "impl" else item
        for item in index.workflows
    )
    next_index = index.evolve(
        parent_digest=run_index_digest(index),
        workflows=workflows,
        updated_at="2026-08-06T00:02:00Z",
    )
    run_store.save(next_index)
    return {
        "run_id": RUN_ID,
        "phase": "req_b",
        "index": _index_state(run_store.get(RUN_ID)),
        "declared_artifact": spec_fact.to_dict(),
    }


async def phase_resume(case_id: str, workspace: str, store_dir: str) -> dict:
    """Phase resume：按 case 恢复。"""
    case = V22CCase(f"{case_id}-resume", RUN_ID, workspace=workspace)
    checkpoint_store = JsonCheckpointStore(os.path.join(store_dir, "checkpoints.json"))
    run_store = JsonRunResumeStore(os.path.join(store_dir, "run-resume.json"))
    workflows = build_workflows(case.workspace)
    index_before = run_store.get(RUN_ID)
    coordinator, workflow_counts = _coordinator(
        case, checkpoint_store, run_store, workflows
    )
    spec_md = os.path.join(case.workspace, "output", "spec.md")
    solution_py = os.path.join(case.workspace, "output", "solution.py")
    report_md = os.path.join(case.workspace, "output", "report.md")
    spec_md_before = _sha(spec_md)
    solution_before = _sha(solution_py)
    report_before = _sha(report_md)

    if case_id == "c08":
        return await _resume_c08(
            case, coordinator, run_store, checkpoint_store,
            workflow_counts, index_before, spec_md_before, solution_before, report_before,
        )
    if case_id == "c05":
        return await _resume_c05(
            case, coordinator, run_store, workflow_counts,
            index_before, spec_md_before, solution_before, report_before,
        )
    if case_id == "c06":
        return await _resume_c06(
            case, coordinator, run_store, workflow_counts,
            index_before, spec_md_before, solution_before, report_before,
        )

    # c02 / c03 / c04 / c07：恢复 B → C
    replay = case_id == "c04"
    request = (
        RunResumeRequest(
            requested_run_id=RUN_ID,
            candidate_run_ids=(RUN_ID,),
            requested_action=ResumeAction.REPLAY_FROM_STAGE,
        )
        if replay
        else None
    )
    try:
        b = await case.run_coordinator(
            coordinator, RUN_ID, attempt_id="attempt-impl-retry", request=request
        )
    except SimulatedCrash as exc:
        return _resume_failure_evidence(
            f"SimulatedCrash:{exc}", case, run_store, index_before,
            spec_md_before, solution_before, report_before,
        )

    b_result = b.execution_result
    b_success = _result_success(b_result)
    b_decision = b.decision

    c_result = None
    c_decision = None
    c_success = False
    if b_success:
        c = await case.run_coordinator(
            coordinator, RUN_ID, attempt_id="attempt-verify"
        )
        c_result = c.execution_result
        c_decision = c.decision
        c_success = _result_success(c_result)

    return _resume_bc_evidence(
        case, coordinator, run_store, workflow_counts, index_before,
        spec_md_before, solution_before, report_before,
        b_result, b_decision, b_success,
        c_result, c_decision, c_success,
    )


async def _resume_c05(
    case, coordinator, run_store, workflow_counts, index_before,
    spec_md_before, solution_before, report_before,
) -> dict:
    """[c05] 只恢复 C（verify），随后再次调用 Coordinator 应无任何执行。"""
    c = await case.run_coordinator(coordinator, RUN_ID, attempt_id="attempt-verify-retry")
    c_result = c.execution_result
    c_success = _result_success(c_result)
    second = None
    second_result = None
    if c_success:
        second = await case.run_coordinator(
            coordinator, RUN_ID, attempt_id="attempt-after-complete"
        )
        second_result = second.execution_result
    index_after = run_store.get(RUN_ID)
    spec_md = os.path.join(case.workspace, "output", "spec.md")
    solution_py = os.path.join(case.workspace, "output", "solution.py")
    report_md = os.path.join(case.workspace, "output", "report.md")
    return {
        "run_id": RUN_ID,
        "phase": "resume",
        "c_success": c_success,
        "c_error": _result_error(c_result),
        "c_decision": _decision_dict(c.decision),
        "second_call_decision": _decision_dict(second.decision) if second else None,
        "second_call_result": (
            "NO_EXECUTION" if second_result is None else second_result.error
        ),
        "workflow_counts": dict(workflow_counts),
        "execution_counts": dict(case.execution_counts),
        "llm_counts": dict(case.llm_counts),
        "tool_counts": dict(case.tool_counts),
        "spec_md_hash_before": spec_md_before,
        "spec_md_hash_after": _sha(spec_md),
        "solution_hash_before": solution_before,
        "solution_hash_after": _sha(solution_py),
        "report_hash_before": report_before,
        "report_hash_after": _sha(report_md),
        "spec_unchanged": spec_md_before == _sha(spec_md),
        "solution_unchanged": solution_before == _sha(solution_py),
        "report_unchanged": report_before == _sha(report_md),
        "a_b_execution_in_resume": {
            "spec": case.execution_counts.get("spec", 0),
            "impl": case.execution_counts.get("impl", 0),
        },
        "index": _index_state(index_after),
        "revision_advanced": (
            index_after is not None
            and index_before is not None
            and index_after.revision > index_before.revision
        ),
    }


async def _resume_c06(
    case, coordinator, run_store, workflow_counts, index_before,
    spec_md_before, solution_before, report_before,
) -> dict:
    """[c06] 上游 spec.md 被篡改：恢复 B 必须在 Provider/Tool 调用前阻断。"""
    b = await case.run_coordinator(
        coordinator, RUN_ID, attempt_id="attempt-impl-retry"
    )
    b_result = b.execution_result
    index_after = run_store.get(RUN_ID)
    return {
        "run_id": RUN_ID,
        "phase": "resume",
        "b_success": _result_success(b_result),
        "b_decision": _decision_dict(b.decision),
        "workflow_counts": dict(workflow_counts),
        "execution_counts": dict(case.execution_counts),
        "llm_counts": dict(case.llm_counts),
        "tool_counts": dict(case.tool_counts),
        "spec_md_hash_before": spec_md_before,
        "spec_md_hash_after": _sha(os.path.join(
            case.workspace, "output", "spec.md"
        )),
        "solution_exists": os.path.exists(os.path.join(
            case.workspace, "output", "solution.py"
        )),
        "index": _index_state(index_after),
        "impl_completed": (
            index_after is not None
            and "impl" in index_after.completed_workflow_ids
        ),
    }


async def _resume_c08(
    case, coordinator, run_store, checkpoint_store, workflow_counts, index_before,
    spec_md_before, solution_before, report_before,
) -> dict:
    """[c08] 错误 Run / Workflow 身份：Provider 调用前拒绝，Store 不被推进。"""
    from agent.run_resume import RunResumeRequest

    ctx = case.make_context(build_workflows(case.workspace)["impl"])
    revision_before = index_before.revision if index_before else None
    cp_count_before = len(checkpoint_store.history(RUN_ID)) if index_before else 0
    files_before = snapshot_files(case.workspace)

    wrong_run = await coordinator.resume_active(
        RUN_ID,
        ctx,
        request=RunResumeRequest(
            requested_run_id="run-OTHER",
            candidate_run_ids=(RUN_ID,),
        ),
    )
    wrong_flow = await coordinator.resume_active(
        RUN_ID,
        ctx,
        request=RunResumeRequest(
            requested_run_id=RUN_ID,
            candidate_run_ids=(RUN_ID,),
            requested_workflow_id="verify",
        ),
    )
    index_after = run_store.get(RUN_ID)
    files_after = snapshot_files(case.workspace)
    return {
        "run_id": RUN_ID,
        "phase": "resume",
        "wrong_run_decision": _decision_dict(wrong_run.decision),
        "wrong_run_result": (
            "NO_EXECUTION" if wrong_run.execution_result is None else "EXECUTED"
        ),
        "wrong_flow_decision": _decision_dict(wrong_flow.decision),
        "wrong_flow_result": (
            "NO_EXECUTION" if wrong_flow.execution_result is None else "EXECUTED"
        ),
        "workflow_counts": dict(workflow_counts),
        "execution_counts": dict(case.execution_counts),
        "llm_counts": dict(case.llm_counts),
        "tool_counts": dict(case.tool_counts),
        "revision_before": revision_before,
        "revision_after": index_after.revision if index_after else None,
        "revision_unchanged": (
            (index_after.revision if index_after else None) == revision_before
        ),
        "checkpoint_count_before": cp_count_before,
        "checkpoint_count_after": len(checkpoint_store.history(RUN_ID)),
        "workspace_unchanged": files_before == files_after,
        "index": _index_state(index_after),
    }


def _resume_bc_evidence(
    case, coordinator, run_store, workflow_counts, index_before,
    spec_md_before, solution_before, report_before,
    b_result, b_decision, b_success,
    c_result, c_decision, c_success,
) -> dict:
    index_after = run_store.get(RUN_ID)
    return {
        "run_id": RUN_ID,
        "phase": "resume",
        "a_checkpoint_loaded": (
            index_before is not None
            and "spec" in index_before.completed_workflow_ids
            and "spec" not in index_before.pending_workflow_ids
        ),
        "b_success": b_success,
        "b_error": _result_error(b_result),
        "b_decision": _decision_dict(b_decision),
        "c_success": c_success,
        "c_error": _result_error(c_result),
        "c_decision": _decision_dict(c_decision),
        "workflow_counts": dict(workflow_counts),
        "execution_counts": dict(case.execution_counts),
        "llm_counts": dict(case.llm_counts),
        "tool_counts": dict(case.tool_counts),
        "spec_md_hash_before": spec_md_before,
        "spec_md_hash_after": _sha(os.path.join(
            case.workspace, "output", "spec.md"
        )),
        "spec_md_unchanged": spec_md_before == _sha(os.path.join(
            case.workspace, "output", "spec.md"
        )),
        "solution_exists": os.path.exists(os.path.join(
            case.workspace, "output", "solution.py"
        )),
        "solution_hash_before": solution_before,
        "solution_hash_after": _sha(os.path.join(
            case.workspace, "output", "solution.py"
        )),
        "solution_unchanged": solution_before == _sha(os.path.join(
            case.workspace, "output", "solution.py"
        )),
        "report_exists": os.path.exists(os.path.join(
            case.workspace, "output", "report.md"
        )),
        "side_effects": case.ledger.all(),
        "duplicate_side_effects": {
            k: v for k, v in case.ledger.all().items() if v > 1
        },
        "index": _index_state(index_after),
        "revision_advanced": (
            index_after is not None
            and index_before is not None
            and index_after.revision > index_before.revision
        ),
        "revision_gap": (
            index_after.revision - index_before.revision
            if index_after is not None and index_before is not None
            else None
        ),
    }


def _resume_failure_evidence(
    error: str,
    case, run_store, index_before,
    spec_md_before, solution_before, report_before,
) -> dict:
    index_after = run_store.get(RUN_ID)
    return {
        "run_id": RUN_ID,
        "phase": "resume",
        "resume_error": error,
        "b_success": False,
        "c_success": False,
        "workflow_counts": {},
        "execution_counts": dict(case.execution_counts),
        "llm_counts": dict(case.llm_counts),
        "tool_counts": dict(case.tool_counts),
        "spec_md_hash_before": spec_md_before,
        "spec_md_hash_after": _sha(os.path.join(
            case.workspace, "output", "spec.md"
        )),
        "spec_md_unchanged": spec_md_before == _sha(os.path.join(
            case.workspace, "output", "spec.md"
        )),
        "solution_exists": os.path.exists(os.path.join(
            case.workspace, "output", "solution.py"
        )),
        "solution_hash_before": solution_before,
        "solution_hash_after": _sha(os.path.join(
            case.workspace, "output", "solution.py"
        )),
        "report_exists": os.path.exists(os.path.join(
            case.workspace, "output", "report.md"
        )),
        "side_effects": case.ledger.all(),
        "duplicate_side_effects": {
            k: v for k, v in case.ledger.all().items() if v > 1
        },
        "index": _index_state(index_after),
        "revision_advanced": (
            index_after is not None
            and index_before is not None
            and index_after.revision > index_before.revision
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--case",
        choices=("c02", "c03", "c04", "c05", "c06", "c07", "c08"),
        default="c02",
    )
    parser.add_argument(
        "--phase",
        choices=("a", "b_int", "b_full", "crash", "c_crash", "req_b", "resume"),
        required=True,
    )
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--store", required=True)
    args = parser.parse_args()

    if args.phase == "a":
        ev = asyncio.run(
            phase_a(
                args.workspace,
                args.store,
                impl_idempotent=(args.case == "c04"),
            )
        )
    elif args.phase == "b_int":
        if args.case not in ("c03", "c04", "c08"):
            raise SystemExit("--phase b_int 仅用于 c03/c04/c08")
        ev = asyncio.run(
            _activate_impl(
                args.workspace,
                args.store,
                interrupt_after_stage_id="read_spec",
            )
        )
    elif args.phase == "b_full":
        if args.case != "c05":
            raise SystemExit("--phase b_full 仅用于 c05")
        ev = asyncio.run(_run_impl_full(args.workspace, args.store))
    elif args.phase == "crash":
        if args.case != "c07":
            raise SystemExit("--phase crash 仅用于 c07")
        try:
            ev = asyncio.run(
                _activate_impl(
                    args.workspace,
                    args.store,
                    crash_after_write_path="solution.py",
                )
            )
        except SimulatedCrash as exc:
            print(f"SIMULATED_CRASH {exc}", file=sys.stderr)
            raise SystemExit(70) from exc
    elif args.phase == "c_crash":
        if args.case != "c05":
            raise SystemExit("--phase c_crash 仅用于 c05")
        try:
            ev = asyncio.run(_crash_verify(args.workspace, args.store))
        except SimulatedCrash as exc:
            print(f"SIMULATED_CRASH {exc}", file=sys.stderr)
            raise SystemExit(70) from exc
    elif args.phase == "req_b":
        if args.case != "c06":
            raise SystemExit("--phase req_b 仅用于 c06")
        ev = asyncio.run(phase_req_b(args.workspace, args.store))
    else:
        ev = asyncio.run(
            phase_resume(args.case, args.workspace, args.store)
        )
    out = os.path.join(args.store, f"evidence_{args.phase}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(ev, f, ensure_ascii=False, indent=2)
    print(f"phase={args.phase} written={out}")


if __name__ == "__main__":
    main()
