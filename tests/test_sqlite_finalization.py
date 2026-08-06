"""v2.3B-3 Finalization Bundle transaction and recovery tests."""

from __future__ import annotations

import json
import concurrent.futures
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from agent.checkpoint import ArtifactSnapshot, CheckpointStatus, RunCheckpoint
from agent.run_resume import (
    RunArtifactFact,
    RunResumeIndex,
    RunWorkflowStatus,
    WorkflowDependency,
    WorkflowSummary,
)
from agent.runtime_store import (
    ArtifactCommitFact,
    DurableStoreError,
    FinalizationBundle,
    FinalizationFailurePoint,
    SqliteRuntimeStore,
    StoreErrorCode,
)


RESULT_DIGEST = "external-result-digest-1"
ARTIFACT_DIGEST = "artifact-digest-1"


def _prepare_store(path: Path) -> SqliteRuntimeStore:
    store = SqliteRuntimeStore.open(path)
    store.initialize_run("tenant-a", "session-a", "run-1", "request-init")
    store.acquire_fence("tenant-a", "session-a", "run-1", "writer-a")
    store.prepare_operation(
        "tenant-a",
        "session-a",
        "run-1",
        request_id="request-prepare",
        writer_id="writer-a",
        fence_token=1,
        expected_revision=0,
        expected_parent_digest="",
        idempotency_key="effect-1",
        operation_type="filesystem.write",
        request_digest="request-digest-1",
        expected_effect_digest=RESULT_DIGEST,
        external_reference="output/result.txt",
    )
    return store


def _build_bundle(store: SqliteRuntimeStore) -> FinalizationBundle:
    head = store.get_run_head("tenant-a", "run-1", session_id="session-a")
    assert head is not None
    intent = store.get_idempotency(
        "tenant-a",
        "run-1",
        "effect-1",
        session_id="session-a",
    )
    assert intent is not None
    parent_row = store.connection.execute(
        """
        SELECT payload_digest
        FROM run_resume_revisions
        WHERE tenant_id = ? AND run_id = ? AND revision = ?
        """,
        ("tenant-a", "run-1", intent.prepared_revision),
    ).fetchone()
    assert parent_row is not None
    parent_digest = str(parent_row["payload_digest"])

    checkpoint = RunCheckpoint(
        run_id="run-1",
        checkpoint_id="cp-1",
        parent_checkpoint_id=None,
        sequence_number=0,
        session_id="session-a",
        conversation_id="conversation-a",
        user_scope="user-a",
        workflow_id="wf-1",
        workflow_version="1.0.0",
        plan_version="1.0.0",
        active_stage_id="stage-1",
        active_task_id="task-1",
        status=CheckpointStatus.COMPLETED,
        execution_plan={"steps": [{"id": "stage-1", "verified": True}]},
        target_summary="output/result.txt",
        activation_attempt_id="attempt-1",
        artifacts=(
            ArtifactSnapshot(
                artifact_id="artifact-1",
                artifact_type="text",
                digest=ARTIFACT_DIGEST,
                reference="output/result.txt",
                exists=True,
            ),
        ),
        verifier_status="VERIFIED",
        checkpoint_schema_version="1.0",
        contract_version="v2.2A",
        created_at="2026-08-06T00:00:00Z",
        updated_at="2026-08-06T00:00:01Z",
    )
    artifact = ArtifactCommitFact(
        artifact_id="artifact-1",
        artifact_type="text",
        reference="output/result.txt",
        digest=ARTIFACT_DIGEST,
        producer_workflow_id="wf-1",
        producer_stage_id="stage-1",
        exists=True,
        verified=True,
        verification_evidence_digest="evidence-digest-1",
    )
    summary = WorkflowSummary(
        workflow_id="wf-1",
        workflow_version="1.0.0",
        status=RunWorkflowStatus.COMPLETED,
        checkpoint_id=checkpoint.checkpoint_id,
        activation_attempt_id=checkpoint.activation_attempt_id,
        verifier_status="VERIFIED",
    )
    index = RunResumeIndex(
        run_id="run-1",
        workflow_sequence=("wf-1",),
        workflows=(summary,),
        completed_workflow_ids=("wf-1",),
        active_workflow_id="",
        active_checkpoint_id="",
        pending_workflow_ids=(),
        workflow_dependencies=(WorkflowDependency("wf-1"),),
        artifacts=(
            RunArtifactFact(
                artifact_id=artifact.artifact_id,
                producer_workflow_id="wf-1",
                digest=artifact.digest,
                exists=True,
                verified=True,
                artifact_type=artifact.artifact_type,
                reference=artifact.reference,
                producer_stage_id=artifact.producer_stage_id,
            ),
        ),
        store_generation=store.store_generation,
        index_version="v2.3B-3",
        revision=intent.prepared_revision + 1,
        parent_digest=parent_digest,
        created_at="2026-08-06T00:00:00Z",
        updated_at="2026-08-06T00:00:01Z",
        session_id="session-a",
        conversation_id="conversation-a",
        user_scope="user-a",
    )
    return FinalizationBundle(
        tenant_id="tenant-a",
        session_id="session-a",
        run_id="run-1",
        workflow_id="wf-1",
        request_id="request-finalize",
        writer_id=head.current_writer_id,
        fence_epoch=head.current_fence_token,
        expected_revision=intent.prepared_revision,
        expected_parent_digest=parent_digest,
        idempotency_key="effect-1",
        operation_type="filesystem.write",
        request_digest="request-digest-1",
        checkpoint=checkpoint,
        artifacts=(artifact,),
        next_run_index=index,
        external_result_digest=RESULT_DIGEST,
        verifier_status="VERIFIED",
    )


def _table_count(store: SqliteRuntimeStore, table: str) -> int:
    allowed = {"checkpoints", "artifact_metadata", "run_resume_revisions"}
    assert table in allowed
    row = store.connection.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()
    assert row is not None
    return int(row["count"])


def _assert_prepared_only(
    store: SqliteRuntimeStore,
    *,
    artifact_count: int = 0,
) -> None:
    head = store.get_run_head("tenant-a", "run-1", session_id="session-a")
    assert head is not None
    assert head.current_revision == 1
    intent = store.get_idempotency("tenant-a", "run-1", "effect-1", session_id="session-a")
    assert intent is not None
    assert intent.effect_state == "PREPARED"
    assert intent.committed_revision is None
    assert _table_count(store, "checkpoints") == 0
    assert _table_count(store, "artifact_metadata") == artifact_count
    assert _table_count(store, "run_resume_revisions") == 1


def test_finalization_commits_all_facts_once_and_retries_idempotently(
    tmp_path: Path,
) -> None:
    store = _prepare_store(tmp_path / "finalize.sqlite")
    try:
        bundle = _build_bundle(store)
        result = store.finalize_bundle(bundle)
        assert result.idempotent is False
        assert result.effect_state == "COMMITTED"
        assert result.run_revision == 2
        assert _table_count(store, "checkpoints") == 1
        assert _table_count(store, "artifact_metadata") == 1
        assert _table_count(store, "run_resume_revisions") == 2

        intent = store.get_idempotency("tenant-a", "run-1", "effect-1")
        assert intent is not None
        assert intent.effect_state == "COMMITTED"
        assert intent.result_digest == RESULT_DIGEST
        assert intent.committed_revision == 2

        head = store.get_run_head("tenant-a", "run-1")
        assert head is not None
        assert head.current_revision == 2
        assert head.current_digest == result.run_index_digest
        assert head.run_status == "COMPLETED"

        retry = store.finalize_bundle(bundle)
        assert retry.idempotent is True
        assert replace(retry, idempotent=False) == result
        assert _table_count(store, "checkpoints") == 1
        assert _table_count(store, "run_resume_revisions") == 2
    finally:
        store.close()


@pytest.mark.parametrize("failure_point", list(FinalizationFailurePoint))
def test_finalization_failure_at_each_internal_boundary_rolls_back(
    tmp_path: Path,
    failure_point: FinalizationFailurePoint,
) -> None:
    store = _prepare_store(tmp_path / f"rollback-{failure_point.value}.sqlite")
    try:
        with pytest.raises(DurableStoreError) as error:
            store.finalize_bundle(_build_bundle(store), failure_point=failure_point)
        assert error.value.code is StoreErrorCode.FINALIZATION_INJECTED_FAILURE
        _assert_prepared_only(store)
    finally:
        store.close()


def test_same_key_different_final_digest_is_rejected(tmp_path: Path) -> None:
    store = _prepare_store(tmp_path / "result-conflict.sqlite")
    try:
        bundle = _build_bundle(store)
        store.finalize_bundle(bundle)
        with pytest.raises(DurableStoreError) as error:
            store.finalize_bundle(replace(bundle, external_result_digest="other-result"))
        assert error.value.code is StoreErrorCode.FINALIZATION_CONFLICT
        assert _table_count(store, "checkpoints") == 1
        assert _table_count(store, "run_resume_revisions") == 2
    finally:
        store.close()


def test_two_connections_competing_finalize_have_one_commit_and_one_retry(
    tmp_path: Path,
) -> None:
    path = tmp_path / "finalize-race.sqlite"
    store = _prepare_store(path)
    store.close()

    def attempt() -> bool:
        connection = SqliteRuntimeStore.open(path)
        try:
            return connection.finalize_bundle(_build_bundle(connection)).idempotent
        finally:
            connection.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        idempotent_flags = list(pool.map(lambda _: attempt(), range(2)))
    assert sorted(idempotent_flags) == [False, True]

    check = SqliteRuntimeStore.open(path)
    try:
        assert _table_count(check, "checkpoints") == 1
        assert _table_count(check, "artifact_metadata") == 1
        assert _table_count(check, "run_resume_revisions") == 2
    finally:
        check.close()


def test_existing_artifact_digest_failure_rolls_back_checkpoint_and_head(
    tmp_path: Path,
) -> None:
    store = _prepare_store(tmp_path / "artifact-conflict.sqlite")
    try:
        store.connection.execute(
            """
            INSERT INTO artifact_metadata
                (tenant_id, session_id, run_id, artifact_id, artifact_type,
                 digest, reference, exists_flag, verified,
                 verification_evidence_digest, producer_workflow_id,
                 producer_stage_id, producer_task_id, created_revision,
                 last_updated_revision, request_id, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1, ?, ?, ?, '', 1, 1, ?, ?)
            """,
            (
                "tenant-a",
                "session-a",
                "run-1",
                "artifact-1",
                "text",
                "old-digest",
                "output/result.txt",
                "old-evidence",
                "wf-1",
                "stage-1",
                "old-request",
                "2026-08-06T00:00:00Z",
            ),
        )
        with pytest.raises(DurableStoreError) as error:
            store.finalize_bundle(_build_bundle(store))
        assert error.value.code is StoreErrorCode.ARTIFACT_DIGEST_MISMATCH
        _assert_prepared_only(store, artifact_count=1)
        assert _table_count(store, "artifact_metadata") == 1
    finally:
        store.close()


def test_takeover_makes_old_writer_finalization_stale(tmp_path: Path) -> None:
    store = _prepare_store(tmp_path / "stale-finalize.sqlite")
    try:
        bundle = _build_bundle(store)
        takeover = store.takeover_fence(
            "tenant-a",
            "session-a",
            "run-1",
            "writer-b",
            expected_fence_token=1,
        )
        assert takeover.fence_token == 2
        with pytest.raises(DurableStoreError) as error:
            store.finalize_bundle(bundle)
        assert error.value.code is StoreErrorCode.STALE_WRITER
        _assert_prepared_only(store)
    finally:
        store.close()


def test_unverified_artifact_cannot_complete_workflow(tmp_path: Path) -> None:
    store = _prepare_store(tmp_path / "unverified.sqlite")
    try:
        bundle = _build_bundle(store)
        bad_artifact = replace(bundle.artifacts[0], verified=False)
        with pytest.raises(DurableStoreError) as error:
            store.finalize_bundle(replace(bundle, artifacts=(bad_artifact,)))
        assert error.value.code is StoreErrorCode.ARTIFACT_VERIFICATION_FAILED
        _assert_prepared_only(store)
    finally:
        store.close()


def test_checkpoint_lineage_error_does_not_advance_run(tmp_path: Path) -> None:
    store = _prepare_store(tmp_path / "lineage.sqlite")
    try:
        bundle = _build_bundle(store)
        bad_checkpoint = replace(
            bundle.checkpoint,
            checkpoint_id="cp-2",
            sequence_number=1,
            parent_checkpoint_id="missing-parent",
        )
        summary = bundle.next_run_index.workflow("wf-1")
        assert summary is not None
        bad_index = replace(
            bundle.next_run_index,
            workflows=(replace(summary, checkpoint_id="cp-2"),),
        )
        with pytest.raises(DurableStoreError) as error:
            store.finalize_bundle(
                replace(bundle, checkpoint=bad_checkpoint, next_run_index=bad_index)
            )
        assert error.value.code is StoreErrorCode.CHECKPOINT_LINEAGE_CONFLICT
        _assert_prepared_only(store)
    finally:
        store.close()


def test_unknown_external_result_cannot_become_committed(tmp_path: Path) -> None:
    store = _prepare_store(tmp_path / "unknown-result.sqlite")
    try:
        store.connection.execute(
            """
            UPDATE idempotency_ledger
            SET effect_state = 'UNKNOWN'
            WHERE tenant_id = ? AND run_id = ? AND idempotency_key = ?
            """,
            ("tenant-a", "run-1", "effect-1"),
        )
        with pytest.raises(DurableStoreError) as error:
            store.finalize_bundle(_build_bundle(store))
        assert error.value.code is StoreErrorCode.EFFECT_STATE_CONFLICT
        assert _table_count(store, "checkpoints") == 0
    finally:
        store.close()


def test_completed_workflow_without_terminal_artifact_is_rejected(
    tmp_path: Path,
) -> None:
    store = _prepare_store(tmp_path / "terminal-output.sqlite")
    try:
        bundle = _build_bundle(store)
        summary = bundle.next_run_index.workflow("wf-1")
        assert summary is not None
        empty_index = replace(bundle.next_run_index, artifacts=())
        with pytest.raises(DurableStoreError) as error:
            store.finalize_bundle(replace(bundle, artifacts=(), next_run_index=empty_index))
        assert error.value.code is StoreErrorCode.TERMINAL_OUTPUT_MISSING
        _assert_prepared_only(store)
    finally:
        store.close()


def test_process_restart_after_commit_returns_original_result_without_duplication(
    tmp_path: Path,
) -> None:
    path = tmp_path / "process-finalize.sqlite"
    store = _prepare_store(path)
    generation = store.store_generation
    store.close()

    commit_script = """
import os
import sys
from agent.runtime_store import SqliteRuntimeStore
from tests.test_sqlite_finalization import _build_bundle

store = SqliteRuntimeStore.open(sys.argv[1], expected_store_generation=sys.argv[2])
store.finalize_bundle(_build_bundle(store))
os._exit(0)
"""
    committed = subprocess.run(
        [sys.executable, "-c", commit_script, str(path), generation],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
    )
    assert committed.returncode == 0, committed.stderr

    retry_script = """
import json
import sys
from agent.runtime_store import SqliteRuntimeStore
from tests.test_sqlite_finalization import _build_bundle

store = SqliteRuntimeStore.open(sys.argv[1], expected_store_generation=sys.argv[2])
result = store.finalize_bundle(_build_bundle(store))
print(json.dumps(result.to_dict(), sort_keys=True))
store.close()
"""
    retried = subprocess.run(
        [sys.executable, "-c", retry_script, str(path), generation],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(retried.stdout)
    assert payload["idempotent"] is True
    assert payload["effect_state"] == "COMMITTED"
    assert payload["run_revision"] == 2

    reopened = SqliteRuntimeStore.open(path, expected_store_generation=generation)
    try:
        assert _table_count(reopened, "checkpoints") == 1
        assert _table_count(reopened, "run_resume_revisions") == 2
    finally:
        reopened.close()
