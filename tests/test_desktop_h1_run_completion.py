"""Desktop-H1 regressions for Run-scoped completion truth."""

from pathlib import Path
from types import SimpleNamespace
import hashlib

from agent.orchestrator.finalizer import Finalizer
from agent.runtime_store import (
    ArtifactCommitFact,
    DurableRuntimeStoreView,
    SqliteRuntimeStore,
)
from agent.service.contracts import RunLookupRequest, StartRunRequest
from agent.service.run_projector import RunProjector, encode_request_reference
from agent.service.runtime_launcher import RuntimeExecutionLauncher
from agent.services.workspace_service import WorkspaceService


def test_finalizer_verifies_write_in_run_context_workspace(tmp_path: Path) -> None:
    """A successful write in one Run must not be checked against global ROOT."""

    run_workspace = WorkspaceService.scoped(
        tmp_path / "run-a",
        build_index=False,
        lazy_index=True,
    )
    try:
        run_workspace.write_text("output/result.txt", "desktop-h1")
        state = {
            "plan": [
                {
                    "verb": "write",
                    "target": "output/result.txt",
                    "status": "succeeded",
                }
            ]
        }

        assert Finalizer._verify_written_files(
            state,
            "已成功写入：output/result.txt",
            workspace=run_workspace,
        ) is None
    finally:
        run_workspace.close()


def test_finalizer_does_not_accept_missing_run_workspace_file(
    tmp_path: Path,
) -> None:
    """A same-named file outside the Run cannot satisfy its write claim."""

    run_workspace = WorkspaceService.scoped(
        tmp_path / "run-b",
        build_index=False,
        lazy_index=True,
    )
    (tmp_path / "global" / "output").mkdir(parents=True)
    (tmp_path / "global" / "output" / "result.txt").write_text(
        "stale-global-result",
        encoding="utf-8",
    )
    try:
        state = {
            "plan": [
                {
                    "verb": "write",
                    "target": "output/result.txt",
                    "status": "succeeded",
                }
            ]
        }
        correction = Finalizer._verify_written_files(
            state,
            "已成功写入：output/result.txt",
            workspace=run_workspace,
        )
        assert correction is not None
        assert "output/result.txt" in correction
    finally:
        run_workspace.close()


def test_terminal_transition_persists_and_projects_verified_artifact(
    tmp_path: Path,
) -> None:
    """Desktop artifact reads must survive a Service/SQLite reopen."""

    database = tmp_path / "runtime.sqlite"
    request = StartRunRequest(
        tenant_id="tenant-h1",
        user_id="user-h1",
        session_id="session-h1",
        request_id="request-h1",
        request_text="创建 output/result.txt",
        run_id="run-h1",
    )
    store = SqliteRuntimeStore.open(database)
    view = None
    try:
        reservation = store.reserve_service_start(
            request.tenant_id,
            request.session_id,
            requested_run_id=request.run_id,
            request_id=request.request_id,
            request_digest=request.request_digest,
            writer_id="writer-h1",
            external_reference=encode_request_reference(
                request,
                run_id=request.run_id or "",
            ),
        )
        assert reservation.created is True
        view = DurableRuntimeStoreView(
            store,
            tenant_id=request.tenant_id,
            session_id=request.session_id,
            run_id=request.run_id or "",
            request_id=request.request_id,
            writer_id="writer-h1",
        )
        view.transition_run_with_event(
            run_status="RUNNING",
            event_id="run-started:h1",
            event_type="run_started",
            timestamp="2026-08-11T00:00:00Z",
            payload={"request_id": request.request_id},
            expected_status="CREATED",
        )
        artifact = ArtifactCommitFact(
            artifact_id="artifact-h1-result",
            artifact_type="file",
            reference="output/result.txt",
            digest="sha256:desktop-h1",
            producer_workflow_id="runtime-execution",
            producer_stage_id="task:h1-write",
            producer_task_id="h1-write",
        )
        view.transition_run_with_event(
            run_status="COMPLETED",
            event_id="run-completed:h1",
            event_type="run_completed",
            timestamp="2026-08-11T00:00:01Z",
            payload={"request_id": request.request_id},
            run_output={
                "text": "已成功写入：output/result.txt",
                "artifact_ids": [artifact.artifact_id],
            },
            artifacts=(artifact,),
        )
    finally:
        if view is not None:
            view.close()
        store.close()

    reopened = SqliteRuntimeStore.open(database)
    try:
        read = reopened.read_run_snapshot(
            request.tenant_id,
            request.run_id or "",
            session_id=request.session_id,
        )
        snapshot = RunProjector().project(
            read,
            RunLookupRequest(
                tenant_id=request.tenant_id,
                user_id=request.user_id,
                session_id=request.session_id,
                run_id=request.run_id or "",
                request_id="lookup-h1",
            ),
        )
        assert snapshot.status.value == "COMPLETED"
        assert len(snapshot.artifacts) == 1
        assert snapshot.artifacts[0].artifact_id == "artifact-h1-result"
        assert snapshot.artifacts[0].verified is True
        assert snapshot.output is not None
        assert snapshot.output.artifact_ids == ("artifact-h1-result",)
    finally:
        reopened.close()


def test_get_run_reports_head_until_terminal_commit(tmp_path: Path) -> None:
    """A resume index claiming a completed workflow must not project a public
    COMPLETED status or its indexed artifacts through ``get_run`` before the
    terminal Run transaction commits."""

    import asyncio

    import tests.test_ha_r1_harness as harness
    from agent.run_resume.contracts import (
        RunArtifactFact,
        RunResumeIndex,
        RunWorkflowStatus,
        WorkflowDependency,
        WorkflowSummary,
    )

    service, store = harness.service_at(tmp_path)
    request = StartRunRequest(
        tenant_id="tenant-h1",
        user_id="user-h1",
        session_id="session-h1",
        request_id="request-h1",
        request_text="创建 output/result.txt",
        run_id="run-h1",
    )
    artifact = ArtifactCommitFact(
        artifact_id="artifact-h1-result",
        artifact_type="file",
        reference="output/result.txt",
        digest="sha256:desktop-h1",
        producer_workflow_id="runtime-execution",
        producer_stage_id="task:h1-write",
        producer_task_id="h1-write",
    )
    lookup = RunLookupRequest(
        tenant_id=request.tenant_id,
        user_id=request.user_id,
        session_id=request.session_id,
        run_id=request.run_id or "",
        request_id="lookup-h1",
    )
    view = None

    async def scenario() -> None:
        try:
            reservation = store.reserve_service_start(
                request.tenant_id,
                request.session_id,
                requested_run_id=request.run_id,
                request_id=request.request_id,
                request_digest=request.request_digest,
                writer_id="writer-h1",
                external_reference=encode_request_reference(
                    request,
                    run_id=request.run_id or "",
                ),
            )
            assert reservation.created is True
            nonlocal view
            view = DurableRuntimeStoreView(
                store,
                tenant_id=request.tenant_id,
                session_id=request.session_id,
                run_id=request.run_id or "",
                request_id=request.request_id,
                writer_id="writer-h1",
            )
            # 恢复索引登记的 active Workflow 已标完成并带索引工件，但 Run
            # 终态尚未提交 — head 仍为 RUNNING，公开投影不得推断 COMPLETED。
            view.bootstrap_run_index(RunResumeIndex(
                run_id="run-h1",
                session_id="session-h1",
                workflow_sequence=("wf-done", "wf-act"),
                workflows=(
                    WorkflowSummary(
                        workflow_id="wf-done",
                        workflow_version="1.0.0",
                        status=RunWorkflowStatus.COMPLETED,
                        checkpoint_id="cp-done",
                    ),
                    WorkflowSummary(
                        workflow_id="wf-act",
                        workflow_version="1.0.0",
                        status=RunWorkflowStatus.COMPLETED,
                        checkpoint_id="cp-act",
                        depends_on=("wf-done",),
                    ),
                ),
                completed_workflow_ids=("wf-done",),
                active_workflow_id="wf-act",
                active_checkpoint_id="cp-act",
                pending_workflow_ids=(),
                workflow_dependencies=(
                    WorkflowDependency("wf-done"),
                    WorkflowDependency("wf-act", ("wf-done",)),
                ),
                artifacts=(RunArtifactFact(
                    artifact_id="artifact-ghost",
                    producer_workflow_id="wf-act",
                    digest="sha256:ghost",
                ),),
                created_at="2026-08-11T00:00:00Z",
                updated_at="2026-08-11T00:00:00Z",
            ))

            snapshot = await service.get_run(lookup)
            assert snapshot.status.value == "RUNNING"
            assert snapshot.output is None
            assert snapshot.artifacts == ()

            view.transition_run_with_event(
                run_status="COMPLETED",
                event_id="run-completed:h1",
                event_type="run_completed",
                timestamp="2026-08-11T00:00:01Z",
                payload={"request_id": request.request_id},
                run_output={
                    "text": "已成功写入：output/result.txt",
                    "artifact_ids": [artifact.artifact_id],
                },
                artifacts=(artifact,),
            )
            snapshot = await service.get_run(lookup)
            assert snapshot.status.value == "COMPLETED"
            assert [item.artifact_id for item in snapshot.artifacts] == [
                "artifact-h1-result"
            ]
            assert snapshot.output is not None
        finally:
            if view is not None:
                view.close()
            await service.close()

    asyncio.run(scenario())


def test_launcher_artifact_fact_is_bound_to_run_workspace(tmp_path: Path) -> None:
    run_workspace = WorkspaceService.scoped(
        tmp_path / "run-c",
        build_index=False,
        lazy_index=True,
    )
    try:
        run_workspace.write_text("output/result.txt", "desktop-h1")
        runtime = SimpleNamespace(
            last_run_evidence={
                "verified_artifacts": [
                    {
                            "reference": "output/result.txt",
                            "digest": hashlib.sha256(b"desktop-h1").hexdigest(),
                            "artifact_type": "file",
                        "producer_workflow_id": "runtime-execution",
                        "producer_stage_id": "task:h1-write",
                        "producer_task_id": "h1-write",
                    }
                ]
            },
            run_context=SimpleNamespace(
                run_id="run-c",
                workspace=run_workspace,
            ),
        )
        facts = RuntimeExecutionLauncher._artifact_commit_facts(runtime)
        assert len(facts) == 1
        assert facts[0].reference == "output/result.txt"
        assert facts[0].verified is True
        assert len(facts[0].digest) == 64
    finally:
        run_workspace.close()
