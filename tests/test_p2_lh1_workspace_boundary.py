"""Deterministic P2-LH1 Workspace Boundary closeout tests."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agent.bootstrap import load_all
from agent.executor.plan_executor import PlanExecutor
from agent.executor.verifier import ExecutionArtifacts, ExecutionVerifier
from agent.runtime_context import ApplicationContext
from agent.service import StartRunRequest
from agent.service.context_factory import ServiceContextFactory
from agent.runtime_store import SqliteRuntimeStore
from agent.task import ExecutionPlan, ExecutionStep, Task, Verb
from agent.workflow import ExecutionContext, hydrate_checkpoint_artifacts
from agent.checkpoint.contracts import ArtifactSnapshot
from agent.checkpoint.recorder import fact_digest


def _plan(verb: Verb, *steps: ExecutionStep, target: str = "output/result.txt") -> ExecutionPlan:
    return ExecutionPlan(
        task=Task(
            id=f"lh1-{verb.value}",
            verb=verb,
            target=target,
            target_type="file",
        ),
        steps=list(steps),
    )


@pytest.fixture(autouse=True)
def _load_tools() -> None:
    load_all()


def test_same_relative_path_isolated_between_run_workspaces(tmp_path: Path) -> None:
    app = ApplicationContext()
    session = app.create_session("session-lh1")
    root_a = tmp_path / "run-a"
    root_b = tmp_path / "run-b"
    run_a = session.create_run("run-a", workspace=root_a)
    run_b = session.create_run("run-b", workspace=root_b)
    global_path = Path.cwd() / "output/result.txt"
    global_before = global_path.read_bytes() if global_path.exists() else None
    plan = _plan(
        Verb.WRITE,
        ExecutionStep(
            "filesystem.write",
            {"path": "output/result.txt", "content": "A"},
        ),
    )

    result_a = asyncio.run(PlanExecutor().execute(plan, workspace=run_a.workspace))
    result_b = asyncio.run(
        PlanExecutor().execute(
            _plan(
                Verb.WRITE,
                ExecutionStep(
                    "filesystem.write",
                    {"path": "output/result.txt", "content": "B"},
                ),
            ),
            workspace=run_b.workspace,
        )
    )

    assert result_a["_error"] == ""
    assert result_b["_error"] == ""
    assert (root_a / "output/result.txt").read_text() == "A"
    assert (root_b / "output/result.txt").read_text() == "B"
    global_after = global_path.read_bytes() if global_path.exists() else None
    assert global_after == global_before
    app.close()


def test_copy_move_delete_are_scoped_to_run_workspace(tmp_path: Path) -> None:
    app = ApplicationContext()
    session = app.create_session("session-lh1-ops")
    root = tmp_path / "run-ops"
    run = session.create_run("run-ops", workspace=root)
    source = root / "output/source.txt"
    source.parent.mkdir(parents=True)
    source.write_text("stable\n", encoding="utf-8")
    executor = PlanExecutor()

    copy_result = asyncio.run(
        executor.execute(
            _plan(
                Verb.COPY,
                ExecutionStep(
                    "filesystem.copy",
                    {"source": "output/source.txt", "destination": "output/copied.txt"},
                ),
            ),
            workspace=run.workspace,
        )
    )
    move_result = asyncio.run(
        executor.execute(
            _plan(
                Verb.MOVE,
                ExecutionStep(
                    "filesystem.move",
                    {"source": "output/copied.txt", "destination": "output/moved.txt"},
                ),
            ),
            workspace=run.workspace,
        )
    )
    delete_result = asyncio.run(
        executor.execute(
            _plan(
                Verb.DELETE,
                ExecutionStep("filesystem.delete", {"path": "output/moved.txt"}),
            ),
            workspace=run.workspace,
        )
    )

    assert copy_result["_error"] == ""
    assert move_result["_error"] == ""
    assert delete_result["_error"] == ""
    assert source.exists()
    assert not (root / "output/copied.txt").exists()
    assert not (root / "output/moved.txt").exists()
    app.close()


def test_workspace_escape_is_rejected_before_side_effect(tmp_path: Path) -> None:
    app = ApplicationContext()
    session = app.create_session("session-lh1-escape")
    root = tmp_path / "run-escape"
    run = session.create_run("run-escape", workspace=root)
    outside = tmp_path / "outside.txt"
    plan = _plan(
        Verb.WRITE,
        ExecutionStep(
            "filesystem.write",
            {"path": "../outside.txt", "content": "must not write"},
        ),
    )

    result = asyncio.run(PlanExecutor().execute(plan, workspace=run.workspace))

    assert "WORKSPACE_BOUNDARY_VIOLATION" in result["_error"]
    assert not outside.exists()
    app.close()


def test_verifier_does_not_accept_same_name_file_from_global_or_other_run(
    tmp_path: Path,
) -> None:
    app = ApplicationContext()
    session = app.create_session("session-lh1-verify")
    root_a = tmp_path / "verify-a"
    root_b = tmp_path / "verify-b"
    run_a = session.create_run("verify-a", workspace=root_a)
    run_b = session.create_run("verify-b", workspace=root_b)
    stale = root_a / "output/report.md"
    stale.parent.mkdir(parents=True)
    stale.write_text("stale artifact\n", encoding="utf-8")
    plan = _plan(Verb.WRITE, target="output/report.md")

    verification = ExecutionVerifier().verify(
        plan,
        ExecutionArtifacts(files_written=["output/report.md"]),
        task=plan.task,
        workspace=run_b.workspace,
    )

    assert verification.success is False
    assert "output/report.md" in verification.detail
    app.close()


def test_run_workspace_is_rehydrated_by_service_context_factory(tmp_path: Path) -> None:
    database = tmp_path / "runtime.sqlite3"
    workspace = tmp_path / "durable-workspace"
    store = SqliteRuntimeStore.open(database)
    factory = ServiceContextFactory(store, workspace_root=workspace, writer_id="lh1-writer")
    request = StartRunRequest(
        tenant_id="tenant-lh1",
        user_id="user-lh1",
        session_id="session-lh1-service",
        run_id="run-lh1-service",
        request_id="request-lh1-service",
        request_text="创建报告",
    )

    run = factory.create_run(request, run_id=request.run_id or "")

    assert run.workspace.root == workspace.resolve()
    assert run.workspace.root != Path.cwd().resolve()
    factory.close()
    store.close()


def test_architecture_gate_forbids_global_workspace_resolution_in_production() -> None:
    root = Path(__file__).resolve().parents[1]
    production = (
        root / "agent/executor/plan_executor.py",
        root / "agent/executor/verifier.py",
        root / "agent/orchestrator/executor.py",
        root / "agent/executor/executors/workflow.py",
        root / "agent/validators/file_exists.py",
        root / "agent/validators/min_length.py",
        root / "agent/validators/python_syntax.py",
    )
    forbidden = (
        "from tools.filesystem import ROOT",
        "tools.filesystem.ROOT",
        "Path.cwd()",
        "os.getcwd()",
    )
    for path in production:
        source = path.read_text(encoding="utf-8")
        assert not any(token in source for token in forbidden), path


def test_run_close_preserves_workspace_for_resume(tmp_path: Path) -> None:
    root = tmp_path / "resume-workspace"
    root.mkdir()
    durable = root / "output/checkpoint.txt"
    durable.parent.mkdir()
    durable.write_text("durable", encoding="utf-8")
    app = ApplicationContext()
    session = app.create_session("session-lh1-resume")
    run = session.create_run("run-lh1-resume", workspace=root)
    run.close()

    resumed = session.create_run("run-lh1-resume", workspace=root)

    assert resumed.workspace.root == root.resolve()
    assert durable.read_text(encoding="utf-8") == "durable"
    resumed.close()
    app.close()


def test_checkpoint_artifact_hydration_uses_run_workspace_reference(
    tmp_path: Path,
) -> None:
    root = tmp_path / "hydration-workspace"
    artifact = root / "output/report.md"
    artifact.parent.mkdir(parents=True)
    content = "# run-owned report\n"
    artifact.write_text(content, encoding="utf-8")
    app = ApplicationContext()
    session = app.create_session("session-lh1-hydration")
    run = session.create_run("run-lh1-hydration", workspace=root)
    context = ExecutionContext()
    context.set_var("workspace", run.workspace)
    snapshot = ArtifactSnapshot(
        artifact_id="report",
        artifact_type="report",
        digest=fact_digest(content),
        reference="output/report.md",
        metadata=(("encoding", "utf-8"),),
    )

    report = hydrate_checkpoint_artifacts((snapshot,), context)

    assert report.hydrated_types == ("report",)
    assert context.get_artifact("report") is not None
    assert context.get_artifact("report").storage_uri == "output/report.md"
    run.close()
    app.close()
