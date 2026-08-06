"""v2.2C 隔离运行环境 + 证据采集。

每个 case 独立：
  workspace（临时目录）/ run_id / session / checkpoint store / side-effect ledger
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import tempfile
import time
from dataclasses import replace
from pathlib import Path

from agent.checkpoint import InMemoryCheckpointStore, WorkflowCheckpointRequest
from agent.executor.contract import executor_factory
from agent.executor.executors.workflow import WorkflowExecutor
from agent.services.workspace_service import (
    WorkspaceService,
    set_workspace_service,
)
from agent.workflow import ExecutionContext, Workflow
from agent.workspace.manager import WorkspaceManager


class SideEffectLedger:
    """按 (run_id, workflow_id) 记录写副作用，用于 Duplicate Side Effect 判定。"""

    def __init__(self) -> None:
        self._writes: dict[tuple, list[tuple[str, str]]] = {}

    def record(self, run_id: str, workflow_id: str, path: str, digest: str) -> None:
        self._writes.setdefault((run_id, workflow_id), []).append((path, digest))

    def count(self, run_id: str, workflow_id: str) -> int:
        return len(self._writes.get((run_id, workflow_id), []))

    def all(self) -> dict:
        return {
            f"{r}/{w}": len(ws)
            for (r, w), ws in self._writes.items()
        }


class SimulatedCrash(RuntimeError):
    """测试注入的进程崩溃：模拟真实进程在副作用落盘后、checkpoint 提交前死亡。"""


class CountingExecutor:
    """包装真实 executor 类，按 workflow 统计执行次数（LLM/TOOL）。"""

    def __init__(
        self,
        real_cls,
        ledger: dict,
        workflow_id: str,
        *,
        kind: str = "",
        llm_ledger: dict | None = None,
        tool_ledger: dict | None = None,
        crash_after_write_path: str | None = None,
    ) -> None:
        self._real_cls = real_cls
        self._ledger = ledger
        self._workflow_id = workflow_id
        self._kind = kind
        self._llm_ledger = llm_ledger
        self._tool_ledger = tool_ledger
        self._crash_after_write_path = crash_after_write_path

    async def execute(self, target, context):
        self._ledger[self._workflow_id] = self._ledger.get(self._workflow_id, 0) + 1
        if self._kind == "llm" and self._llm_ledger is not None:
            self._llm_ledger[self._workflow_id] = (
                self._llm_ledger.get(self._workflow_id, 0) + 1
            )
        elif self._kind == "tool" and self._tool_ledger is not None:
            self._tool_ledger[self._workflow_id] = (
                self._tool_ledger.get(self._workflow_id, 0) + 1
            )
        real = self._real_cls()
        result = await real.execute(target, context)
        # C07 故障注入：真实写文件成功后、WorkflowExecutor 记录 Stage checkpoint 前崩溃。
        if (
            self._kind == "tool"
            and self._crash_after_write_path
            and result.success
        ):
            path = str((getattr(target, "inputs", {}) or {}).get("path", ""))
            if path.endswith(self._crash_after_write_path):
                raise SimulatedCrash(
                    f"crash-after-side-effect:{path}"
                )
        return result


class CountingWorkflowExecutor:
    """包装 WorkflowExecutor：按 workflow 统计调用次数，并包装 executor_factory
    按 workflow 统计 Stage/Tool 级执行次数。

    用于 RunResumeCoordinator 路径（phase_a / phase_resume），与
    ``V22CCase.run_workflow`` 的计数口径保持一致。
    """

    def __init__(
        self,
        workflow_counts: dict,
        execution_counts: dict,
        *,
        llm_counts: dict | None = None,
        tool_counts: dict | None = None,
        crash_after_write_path: str | None = None,
        interrupt_workflow_id: str | None = None,
        interrupt_after_stage_id: str | None = None,
    ) -> None:
        self._delegate = WorkflowExecutor()
        self._workflow_counts = workflow_counts
        self._execution_counts = execution_counts
        self._llm_counts = llm_counts
        self._tool_counts = tool_counts
        self._crash_after_write_path = crash_after_write_path
        self._interrupt_workflow_id = interrupt_workflow_id
        self._interrupt_after_stage_id = interrupt_after_stage_id
        self.last_checkpoint_request = None

    async def execute(self, workflow, context, *, checkpoint_request=None):
        self._workflow_counts[workflow.id] = self._workflow_counts.get(workflow.id, 0) + 1
        self.last_checkpoint_request = checkpoint_request
        if (
            checkpoint_request is not None
            and workflow.id == self._interrupt_workflow_id
            and self._interrupt_after_stage_id
        ):
            checkpoint_request = replace(
                checkpoint_request,
                interrupt_after_stage_id=self._interrupt_after_stage_id,
            )
        original = dict(executor_factory._registry)

        def _wrap(name: str, workflow_id: str = workflow.id):
            real_cls = original[name]
            return CountingExecutor(
                real_cls,
                self._execution_counts,
                workflow_id,
                kind=name,
                llm_ledger=self._llm_counts,
                tool_ledger=self._tool_counts,
                crash_after_write_path=self._crash_after_write_path,
            )

        executor_factory._registry["tool"] = lambda: _wrap("tool")
        executor_factory._registry["llm"] = lambda: _wrap("llm")
        try:
            return await self._delegate.execute(
                workflow, context, checkpoint_request=checkpoint_request
            )
        finally:
            executor_factory._registry.clear()
            executor_factory._registry.update(original)


def file_digest(path: str) -> str:
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return ""


def snapshot_files(root: str) -> dict:
    out = {}
    base = Path(root)
    for p in base.rglob("*"):
        if p.is_file():
            out[str(p.relative_to(base))] = file_digest(str(p))
    return out


class V22CCase:
    """一个 v2.2C case 的隔离环境与执行器。"""

    def __init__(self, case_id: str, run_id: str, workspace: str | None = None) -> None:
        self.case_id = case_id
        self.run_id = run_id
        self._owns_workspace = workspace is None
        self.workspace = workspace or tempfile.mkdtemp(prefix=f"v22c-{case_id}-")
        self.checkpoint_store = InMemoryCheckpointStore()
        self.ledger = SideEffectLedger()
        self.execution_counts: dict[str, int] = {}
        self.llm_counts: dict[str, int] = {}
        self.tool_counts: dict[str, int] = {}
        self.provider_calls: dict[str, int] = {}
        self.evidence: dict = {}
        self._ctx: ExecutionContext | None = None
        # 隔离 workspace：覆盖 workspace service 与文件工具 ROOT（测试层注入）
        os.makedirs(os.path.join(self.workspace, "output"), exist_ok=True)
        manager = WorkspaceManager(Path(self.workspace))
        set_workspace_service(WorkspaceService(manager))
        import tools.filesystem as _fs
        self._orig_fs_root = _fs.ROOT
        _fs.ROOT = Path(self.workspace).resolve()
        _fs._path_cache.clear()

    def cleanup(self) -> None:
        import tools.filesystem as _fs
        _fs.ROOT = self._orig_fs_root
        if self._owns_workspace:
            shutil.rmtree(self.workspace, ignore_errors=True)

    def _req(self, workflow: Workflow, **kw) -> WorkflowCheckpointRequest:
        return WorkflowCheckpointRequest(
            store=self.checkpoint_store,
            run_id=self.run_id,
            session_id=f"session-{self.case_id}",
            conversation_id=f"conv-{self.case_id}",
            user_scope="v22c",
            plan_version="1.0",
            target_summary=workflow.description,
            **kw,
        )

    def make_context(self, workflow: Workflow) -> ExecutionContext:
        """创建（或复用）工作流执行上下文；同一 Run 内共享，保证 artifact 跨 A/B/C 传递。"""
        if self._ctx is None:
            self._ctx = ExecutionContext(
                workflow_id=workflow.id, user_input=workflow.description
            )
        return self._ctx

    async def run_workflow(
        self,
        workflow: Workflow,
        *,
        interrupt_after_stage_id: str | None = None,
        checkpoint=None,
        context: ExecutionContext | None = None,
    ) -> dict:
        """执行一个 Workflow（含 checkpoint 记录）。返回 {result, checkpoints, files_before, files_after}。"""
        files_before = snapshot_files(self.workspace)
        original = dict(executor_factory._registry)

        def _wrap(name: str, workflow_id: str = workflow.id):
            real_cls = original[name]
            return CountingExecutor(
                real_cls,
                self.execution_counts,
                workflow_id,
                kind=name,
                llm_ledger=self.llm_counts,
                tool_ledger=self.tool_counts,
            )

        executor_factory._registry["tool"] = lambda: _wrap("tool")
        executor_factory._registry["llm"] = lambda: _wrap("llm")
        try:
            ctx = context or self.make_context(workflow)
            req = self._req(workflow, interrupt_after_stage_id=interrupt_after_stage_id, checkpoint=checkpoint)
            result = await WorkflowExecutor().execute(workflow, ctx, checkpoint_request=req)
        finally:
            executor_factory._registry.clear()
            executor_factory._registry.update(original)
        files_after = snapshot_files(self.workspace)
        # 记录写副作用（output/ 下新增或变化的文件）
        for path, digest in files_after.items():
            if files_before.get(path) != digest:
                self.ledger.record(self.run_id, workflow.id, path, digest)
        return {
            "success": result.success,
            "error": result.error if not result.success else "",
            "output": getattr(result, "text", ""),
            "files_before": files_before,
            "files_after": files_after,
        }

    async def run_coordinator(
        self,
        coordinator,
        run_id: str,
        *,
        attempt_id: str,
        context_factory=None,
        request=None,
    ):
        """通过 RunResumeCoordinator 执行一次 activate/resume，采集副作用与计数。

        coordinator 必须使用本 case 的 ``CountingWorkflowExecutor``（workflow_counts /
        execution_counts 传入），这里负责把文件副作用记录进 ledger。
        """
        files_before = snapshot_files(self.workspace)
        execution = await coordinator.execute_or_resume(
            run_id,
            context_factory or (lambda selected: self.make_context(selected)),
            attempt_id=attempt_id,
            request=request,
        )
        files_after = snapshot_files(self.workspace)
        workflow_id = (
            execution.decision.selected_workflow_id
            if execution.decision.selected_workflow_id
            else "?"
        )
        for path, digest in files_after.items():
            if files_before.get(path) != digest:
                self.ledger.record(self.run_id, workflow_id, path, digest)
        return execution

    def artifact_hashes(self) -> dict:
        return {
            "spec.md": file_digest(os.path.join(self.workspace, "output", "spec.md")),
            "solution.py": file_digest(os.path.join(self.workspace, "output", "solution.py")),
            "report.md": file_digest(os.path.join(self.workspace, "output", "report.md")),
        }

    def final_evidence(self) -> dict:
        return {
            "run_id": self.run_id,
            "case_id": self.case_id,
            "execution_counts": dict(self.execution_counts),
            "llm_counts": dict(self.llm_counts),
            "tool_counts": dict(self.tool_counts),
            "side_effect_counts": self.ledger.all(),
            "artifact_hashes": self.artifact_hashes(),
            "provider_calls": dict(self.llm_counts),
        }
