"""v2.3B-4 real subprocess crash/restart harness (R01-R08)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from agent.run_resume.contracts import (
    RunResumeIndex,
    RunWorkflowStatus,
    WorkflowDependency,
    WorkflowSummary,
)
from agent.runtime_store import (
    DurableStoreError,
    DurableRuntimeStoreView,
    FinalizationFailurePoint,
    SqliteRuntimeStore,
    StoreErrorCode,
)
from tests.test_sqlite_finalization import (
    _build_bundle,
    _prepare_store,
    _table_count,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def _run_child(
    tmp_path: Path,
    script: str,
    *args: str | Path,
    expected: int | None = None,
):
    environment = dict(os.environ)
    existing_python_path = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = (
        str(REPO_ROOT)
        + (os.pathsep + existing_python_path if existing_python_path else "")
    )
    completed = subprocess.run(
        [sys.executable, "-c", script, *(str(arg) for arg in args)],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if expected is not None:
        assert completed.returncode == expected, completed.stderr
    return completed


def _assert_prepared_after_restart(path: Path) -> None:
    store = SqliteRuntimeStore.open(path)
    try:
        head = store.get_run_head("tenant-a", "run-1", session_id="session-a")
        assert head is not None and head.current_revision == 1
        intent = store.get_idempotency(
            "tenant-a", "run-1", "effect-1", session_id="session-a"
        )
        assert intent is not None and intent.effect_state == "PREPARED"
        assert intent.committed_revision is None
        assert _table_count(store, "checkpoints") == 0
        assert _table_count(store, "artifact_metadata") == 0
        assert _table_count(store, "run_resume_revisions") == 1
    finally:
        store.close()


def test_r01_prepare_commit_then_process_crash_before_external(tmp_path: Path) -> None:
    path = tmp_path / "r01.sqlite"
    script = """
import os
import sys
from agent.runtime_store import SqliteRuntimeStore

store = SqliteRuntimeStore.open(sys.argv[1])
store.initialize_run('tenant-a', 'session-a', 'run-1', 'request-init')
store.acquire_fence('tenant-a', 'session-a', 'run-1', 'writer-a')
store.prepare_operation(
    'tenant-a', 'session-a', 'run-1', request_id='request-prepare',
    writer_id='writer-a', fence_token=1, expected_revision=0,
    expected_parent_digest='', idempotency_key='effect-1',
    operation_type='filesystem.write', request_digest='request-digest-1',
    expected_effect_digest='external-result-digest-1',
    external_reference='output/result.txt',
)
os._exit(17)
"""
    _run_child(tmp_path, script, path, expected=17)
    _assert_prepared_after_restart(path)


def test_r02_external_effect_then_crash_reconciles_without_duplicate(tmp_path: Path) -> None:
    path = tmp_path / "r02.sqlite"
    store = _prepare_store(path)
    generation = store.store_generation
    store.close()
    script = """
import os
import sys
from pathlib import Path
from agent.runtime_store import SqliteRuntimeStore

store = SqliteRuntimeStore.open(sys.argv[1], expected_store_generation=sys.argv[2])
target = Path('output/result.txt')
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text('external-result-once', encoding='utf-8')
os._exit(17)
"""
    _run_child(tmp_path, script, path, generation, expected=17)
    assert (tmp_path / "output/result.txt").read_text(encoding="utf-8") == "external-result-once"
    store = SqliteRuntimeStore.open(path, expected_store_generation=generation)
    try:
        result = store.finalize_bundle(_build_bundle(store))
        assert result.idempotent is False
        retry = store.finalize_bundle(_build_bundle(store))
        assert retry.idempotent is True
        assert _table_count(store, "checkpoints") == 1
    finally:
        store.close()


@pytest.mark.parametrize(
    "failure_point",
    [
        FinalizationFailurePoint.AFTER_CHECKPOINT_INSERT,
        FinalizationFailurePoint.AFTER_ARTIFACT_METADATA,
        FinalizationFailurePoint.AFTER_INDEX_INSERT,
    ],
)
def test_r03_r04_r05_internal_bundle_crash_rolls_back(
    tmp_path: Path,
    failure_point: FinalizationFailurePoint,
) -> None:
    path = tmp_path / f"{failure_point.value}.sqlite"
    store = _prepare_store(path)
    generation = store.store_generation
    store.close()
    script = """
import os
import sys
from agent.runtime_store import FinalizationFailurePoint, SqliteRuntimeStore
from tests.test_sqlite_finalization import _build_bundle

store = SqliteRuntimeStore.open(sys.argv[1], expected_store_generation=sys.argv[2])
try:
    store.finalize_bundle(
        _build_bundle(store),
        failure_point=FinalizationFailurePoint(sys.argv[3]),
    )
finally:
    os._exit(17)
"""
    _run_child(tmp_path, script, path, generation, failure_point.value, expected=17)
    _assert_prepared_after_restart(path)


def test_r06_commit_then_response_crash_retry_is_stable(tmp_path: Path) -> None:
    path = tmp_path / "r06.sqlite"
    store = _prepare_store(path)
    generation = store.store_generation
    store.close()
    script = """
import os
import sys
from agent.runtime_store import SqliteRuntimeStore
from tests.test_sqlite_finalization import _build_bundle

store = SqliteRuntimeStore.open(sys.argv[1], expected_store_generation=sys.argv[2])
store.finalize_bundle(_build_bundle(store))
os._exit(0)
"""
    _run_child(tmp_path, script, path, generation, expected=0)
    store = SqliteRuntimeStore.open(path, expected_store_generation=generation)
    try:
        retry = store.finalize_bundle(_build_bundle(store))
        assert retry.idempotent is True
        assert retry.run_revision == 2
        assert _table_count(store, "checkpoints") == 1
    finally:
        store.close()


def test_r07_stale_worker_after_takeover_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "r07.sqlite"
    old_store = _prepare_store(path)
    old_bundle = _build_bundle(old_store)
    takeover = SqliteRuntimeStore.open(path)
    try:
        takeover.takeover_fence(
            "tenant-a",
            "session-a",
            "run-1",
            "writer-b",
            expected_fence_token=1,
        )
        stale = replace(old_bundle, writer_id="writer-a", fence_epoch=1)
        with pytest.raises(DurableStoreError) as error:
            old_store.finalize_bundle(stale)
        assert error.value.code is StoreErrorCode.STALE_WRITER
    finally:
        old_store.close()
        takeover.close()


def test_r08_two_processes_compete_for_activation(tmp_path: Path) -> None:
    path = tmp_path / "r08.sqlite"
    store = SqliteRuntimeStore.open(path)
    view = DurableRuntimeStoreView(
        store,
        tenant_id="tenant-a",
        session_id="session-a",
        run_id="run-1",
        request_id="request-init",
        writer_id="bootstrap-writer",
    )
    view.bootstrap_run_index(
        RunResumeIndex(
            run_id="run-1",
            workflow_sequence=("wf-a",),
            workflows=(
                WorkflowSummary(
                    workflow_id="wf-a",
                    workflow_version="1.0.0",
                    status=RunWorkflowStatus.PENDING,
                ),
            ),
            completed_workflow_ids=(),
            active_workflow_id="",
            active_checkpoint_id="",
            pending_workflow_ids=("wf-a",),
            workflow_dependencies=(WorkflowDependency("wf-a"),),
            session_id="session-a",
            conversation_id="conversation-a",
            user_scope="user-a",
        )
    )
    view.close()
    generation = store.store_generation
    store.close()

    script = """
import json
import os
import sys
from agent.runtime_store import DurableRuntimeStoreView, SqliteRuntimeStore

store = SqliteRuntimeStore.open(sys.argv[1], expected_store_generation=sys.argv[2])
try:
    view = DurableRuntimeStoreView(
        store, tenant_id='tenant-a', session_id='session-a', run_id='run-1',
        request_id='request-race', writer_id='writer-race',
    )
    try:
        result = view.activate_workflow(
            'wf-a', expected_revision=1, attempt_id='attempt-' + str(os.getpid())
        )
        payload = {'outcome': 'winner', 'revision': result.revision}
    except Exception as exc:
        payload = {'outcome': type(exc).__name__, 'detail': str(exc)}
    with open(sys.argv[3], 'w', encoding='utf-8') as handle:
        json.dump(payload, handle, sort_keys=True)
    os._exit(0)
except Exception as exc:
    with open(sys.argv[3], 'w', encoding='utf-8') as handle:
        json.dump({'outcome': 'bootstrap-error', 'detail': str(exc)}, handle)
    os._exit(1)
"""
    output_paths = [tmp_path / "race-a.json", tmp_path / "race-b.json"]
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(path), generation, str(output)],
            cwd=tmp_path,
            env={
                **os.environ,
                "PYTHONPATH": str(REPO_ROOT)
                + (
                    os.pathsep + os.environ["PYTHONPATH"]
                    if os.environ.get("PYTHONPATH")
                    else ""
                ),
            },
        )
        for output in output_paths
    ]
    assert [process.wait(timeout=15) for process in processes] == [0, 0]
    outcomes = [
        json.loads(output.read_text(encoding="utf-8"))["outcome"]
        for output in output_paths
    ]
    assert sum(outcome == "winner" for outcome in outcomes) == 1

    reopened = SqliteRuntimeStore.open(path, expected_store_generation=generation)
    try:
        index = reopened.get_run_index(
            "tenant-a", "run-1", session_id="session-a"
        )
        assert index is not None
        assert index.active_workflow_id == "wf-a"
        assert index.workflow("wf-a").activation_attempt_id.startswith("attempt-")
        assert reopened.connection.execute(
            "SELECT COUNT(*) FROM run_resume_revisions WHERE tenant_id = ? AND run_id = ?",
            ("tenant-a", "run-1"),
        ).fetchone()[0] == 2
    finally:
        reopened.close()
