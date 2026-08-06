"""Deterministic v2.3B-2 tests for the SQLite Runtime Store primitives."""

from __future__ import annotations

import concurrent.futures
import io
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from agent.runtime_store import (
    SCHEMA_VERSION,
    DurableStoreError,
    SqliteRuntimeStore,
    StoreErrorCode,
)


def _store(path: Path, *, busy_timeout_ms: int = 5000) -> SqliteRuntimeStore:
    return SqliteRuntimeStore.open(path, busy_timeout_ms=busy_timeout_ms)


def _run_with_fence(store: SqliteRuntimeStore, run_id: str = "run-1") -> int:
    store.initialize_run("tenant-a", "session-a", run_id, "request-init")
    return store.acquire_fence(
        "tenant-a",
        "session-a",
        run_id,
        "writer-a",
    ).fence_token


def _append(
    store: SqliteRuntimeStore,
    *,
    run_id: str = "run-1",
    writer_id: str = "writer-a",
    fence_token: int = 1,
    expected_revision: int = 0,
    expected_parent_digest: str = "",
    expected_store_generation: str | None = None,
    payload: Any = None,
):
    return store.append_revision(
        "tenant-a",
        "session-a",
        run_id,
        request_id="request-revision",
        payload={"value": 1} if payload is None else payload,
        writer_id=writer_id,
        fence_token=fence_token,
        expected_revision=expected_revision,
        expected_parent_digest=expected_parent_digest,
        expected_store_generation=expected_store_generation,
    )


def _prepare(
    store: SqliteRuntimeStore,
    *,
    key: str = "effect-1",
    request_digest: str = "request-digest-1",
    expected_revision: int = 0,
    fence_token: int = 1,
    expected_parent_digest: str = "",
):
    return store.prepare_operation(
        "tenant-a",
        "session-a",
        "run-1",
        request_id="request-prepare",
        writer_id="writer-a",
        fence_token=fence_token,
        expected_revision=expected_revision,
        idempotency_key=key,
        operation_type="filesystem.write",
        request_digest=request_digest,
        expected_parent_digest=expected_parent_digest,
        expected_effect_digest="effect-digest-1",
        external_reference="output/result.txt",
    )


def test_sqlite_bootstrap_verifies_pragmas_and_generation(tmp_path: Path) -> None:
    path = tmp_path / "runtime.sqlite"
    first = SqliteRuntimeStore.open(
        path,
        expected_store_generation="generation-1",
        busy_timeout_ms=45,
        wal_autocheckpoint=77,
    )
    assert first.schema_version == SCHEMA_VERSION
    assert first.store_generation == "generation-1"
    assert first.pragma_snapshot() == {
        "journal_mode": "wal",
        "synchronous": 2,
        "foreign_keys": 1,
        "busy_timeout": 45,
        "wal_autocheckpoint": 77,
    }
    first.close()

    reopened = SqliteRuntimeStore.open(
        path,
        expected_store_generation="generation-1",
    )
    assert reopened.store_generation == "generation-1"
    reopened.close()

    with pytest.raises(DurableStoreError) as error:
        SqliteRuntimeStore.open(path, expected_store_generation="other-generation")
    assert error.value.code is StoreErrorCode.STORE_GENERATION_MISMATCH


def test_fence_acquire_is_monotonic_and_same_owner_retry_is_idempotent(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "fence.sqlite")
    try:
        first = _run_with_fence(store)
        assert first == 1
        retry = store.acquire_fence(
            "tenant-a",
            "session-a",
            "run-1",
            "writer-a",
            expected_fence_token=1,
        )
        assert retry.fence_token == 1
        assert retry.idempotent is True

        store.release_fence("tenant-a", "session-a", "run-1", "writer-a", 1)
        second = store.acquire_fence(
            "tenant-a",
            "session-a",
            "run-1",
            "writer-b",
            expected_fence_token=1,
        )
        assert second.fence_token == 2
        current = store.get_current_fence("tenant-a", "run-1")
        assert current is not None
        assert current.writer_id == "writer-b"
    finally:
        store.close()


def test_takeover_invalidates_old_writer_and_release_cannot_cross_fence(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "takeover.sqlite")
    try:
        _run_with_fence(store)
        takeover = store.takeover_fence(
            "tenant-a",
            "session-a",
            "run-1",
            "writer-b",
            expected_fence_token=1,
        )
        assert takeover.fence_token == 2

        with pytest.raises(DurableStoreError) as stale_write:
            _append(store, writer_id="writer-a", fence_token=1)
        assert stale_write.value.code is StoreErrorCode.STALE_WRITER

        with pytest.raises(DurableStoreError) as stale_release:
            store.release_fence("tenant-a", "session-a", "run-1", "writer-a", 1)
        assert stale_release.value.code is StoreErrorCode.STALE_WRITER
    finally:
        store.close()


def test_revision_cas_with_two_independent_connections_has_one_winner(
    tmp_path: Path,
) -> None:
    path = tmp_path / "cas.sqlite"
    primary = _store(path)
    _run_with_fence(primary)
    primary.close()

    def attempt() -> tuple[str, int | str]:
        connection = _store(path)
        try:
            try:
                revision = _append(connection)
                return "success", revision.revision
            except DurableStoreError as error:
                return "error", error.code.value
        finally:
            connection.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: attempt(), range(2)))

    assert [item[0] for item in results].count("success") == 1
    assert [item[1] for item in results].count(StoreErrorCode.REVISION_CONFLICT.value) == 1

    check = _store(path)
    try:
        head = check.get_run_head("tenant-a", "run-1", session_id="session-a")
        assert head is not None
        assert head.current_revision == 1
        latest = check.get_latest_revision("tenant-a", "run-1")
        assert latest is not None
        assert latest.revision == 1
    finally:
        check.close()


def test_parent_digest_is_part_of_revision_cas(tmp_path: Path) -> None:
    store = _store(tmp_path / "digest.sqlite")
    try:
        _run_with_fence(store)
        with pytest.raises(DurableStoreError) as first_error:
            _append(store, expected_parent_digest="wrong-parent")
        assert first_error.value.code is StoreErrorCode.PARENT_DIGEST_MISMATCH

        revision = _append(store)
        with pytest.raises(DurableStoreError) as second_error:
            _append(
                store,
                expected_revision=revision.revision,
                expected_parent_digest="wrong-parent",
            )
        assert second_error.value.code is StoreErrorCode.PARENT_DIGEST_MISMATCH
    finally:
        store.close()


def test_prepare_same_key_retry_conflict_and_different_key_matrix(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "idempotency.sqlite")
    try:
        _run_with_fence(store)
        prepared = _prepare(store)
        retry = _prepare(
            store,
            expected_revision=999,
            fence_token=999,
        )
        assert retry.operation_id == prepared.operation_id
        assert retry.prepared_revision == prepared.prepared_revision
        assert retry.effect_state == "PREPARED"

        with pytest.raises(DurableStoreError) as conflict:
            _prepare(
                store,
                request_digest="different-request-digest",
                expected_revision=999,
                fence_token=999,
            )
        assert conflict.value.code is StoreErrorCode.IDEMPOTENCY_CONFLICT

        head = store.get_run_head("tenant-a", "run-1")
        assert head is not None
        independent = _prepare(
            store,
            key="effect-2",
            request_digest="request-digest-2",
            expected_revision=1,
            expected_parent_digest=head.current_digest,
        )
        assert independent.operation_id != prepared.operation_id
        assert independent.prepared_revision == 2
        stored = store.get_idempotency("tenant-a", "run-1", "effect-1")
        assert stored is not None
        assert stored.operation_id == prepared.operation_id
    finally:
        store.close()


def test_same_key_committed_retry_returns_the_same_committed_fact(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "idempotency-committed.sqlite")
    try:
        _run_with_fence(store)
        prepared = _prepare(store)
        result_json = '{"ok":true}'
        store.connection.execute("BEGIN IMMEDIATE")
        store.connection.execute(
            """
            UPDATE idempotency_ledger
            SET effect_state = 'COMMITTED', committed_revision = ?,
                result_json = ?, result_digest = ?, updated_at = ?
            WHERE tenant_id = ? AND run_id = ? AND idempotency_key = ?
            """,
            (
                prepared.prepared_revision,
                result_json,
                "result-digest",
                "2026-08-06T00:00:00Z",
                "tenant-a",
                "run-1",
                prepared.idempotency_key,
            ),
        )
        store.connection.execute("COMMIT")

        retry = _prepare(store, expected_revision=999, fence_token=999)
        assert retry.operation_id == prepared.operation_id
        assert retry.effect_state == "COMMITTED"
        assert retry.result_json == result_json
        assert retry.committed_revision == prepared.prepared_revision
        assert retry.run_revision == prepared.prepared_revision
    finally:
        if store.connection.in_transaction:
            store.connection.execute("ROLLBACK")
        store.close()


def test_same_key_concurrent_prepare_creates_one_intent(tmp_path: Path) -> None:
    path = tmp_path / "idempotency-concurrent.sqlite"
    primary = _store(path)
    _run_with_fence(primary)
    primary.close()

    def attempt() -> tuple[str, str]:
        connection = _store(path)
        try:
            prepared = _prepare(connection)
            return prepared.operation_id, prepared.effect_state
        finally:
            connection.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: attempt(), range(2)))
    assert len({item[0] for item in results}) == 1
    assert {item[1] for item in results} == {"PREPARED"}

    check = _store(path)
    try:
        head = check.get_run_head("tenant-a", "run-1")
        assert head is not None
        assert head.current_revision == 1
    finally:
        check.close()


def test_prepare_retry_keeps_original_fence_token_after_takeover(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "prepare-fence.sqlite")
    try:
        _run_with_fence(store)
        prepared = _prepare(store)
        takeover = store.takeover_fence(
            "tenant-a",
            "session-a",
            "run-1",
            "writer-b",
            expected_fence_token=1,
        )
        assert takeover.fence_token == 2
        retry = _prepare(store, expected_revision=999, fence_token=999)
        assert retry.operation_id == prepared.operation_id
        assert retry.fence_epoch == 1
    finally:
        store.close()


@pytest.mark.parametrize(
    "live_value",
    [io.StringIO("not a checkpoint"), lambda: None, (item for item in [1])],
)
def test_revision_payload_rejects_live_objects(
    tmp_path: Path,
    live_value: Any,
) -> None:
    store = _store(tmp_path / "json-boundary.sqlite")
    try:
        _run_with_fence(store)
        with pytest.raises(DurableStoreError) as error:
            _append(store, payload={"live": live_value})
        assert error.value.code is StoreErrorCode.INVALID_ARGUMENT
        head = store.get_run_head("tenant-a", "run-1")
        assert head is not None
        assert head.current_revision == 0
    finally:
        store.close()


def test_identity_and_generation_are_rejected_before_write(tmp_path: Path) -> None:
    path = tmp_path / "identity.sqlite"
    store = _store(path)
    try:
        _run_with_fence(store)
        with pytest.raises(DurableStoreError) as identity:
            store.append_revision(
                "tenant-a",
                "other-session",
                "run-1",
                request_id="request-revision",
                payload={"x": 1},
                writer_id="writer-a",
                fence_token=1,
                expected_revision=0,
                expected_parent_digest="",
            )
        assert identity.value.code is StoreErrorCode.IDENTITY_MISMATCH
        with pytest.raises(DurableStoreError) as generation:
            _append(store, expected_store_generation="wrong-generation")
        assert generation.value.code is StoreErrorCode.STORE_GENERATION_MISMATCH
        head = store.get_run_head("tenant-a", "run-1")
        assert head is not None
        assert head.current_revision == 0
    finally:
        store.close()


def test_reopen_in_a_new_process_rehydrates_fence_revision_and_intent(
    tmp_path: Path,
) -> None:
    path = tmp_path / "reopen.sqlite"
    store = _store(path)
    generation = store.store_generation
    _run_with_fence(store)
    revision = _append(store)
    prepared = _prepare(
        store,
        expected_revision=revision.revision,
        expected_parent_digest=revision.payload_digest,
    )
    store.close()

    script = """
import json
import sys
from agent.runtime_store import SqliteRuntimeStore

store = SqliteRuntimeStore.open(sys.argv[1], expected_store_generation=sys.argv[2])
head = store.get_run_head('tenant-a', 'run-1', session_id='session-a')
intent = store.get_idempotency('tenant-a', 'run-1', 'effect-1', session_id='session-a')
print(json.dumps({
    'revision': head.current_revision if head else None,
    'fence': head.current_fence_token if head else None,
    'writer': head.current_writer_id if head else None,
    'intent': intent.effect_state if intent else None,
}, sort_keys=True))
store.close()
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, str(path), generation],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(completed.stdout) == {
        "revision": prepared.prepared_revision,
        "fence": 1,
        "writer": "writer-a",
        "intent": "PREPARED",
    }


def test_busy_timeout_returns_stable_store_busy(tmp_path: Path) -> None:
    path = tmp_path / "busy.sqlite"
    bootstrap = _store(path)
    _run_with_fence(bootstrap)
    bootstrap.close()
    holder = _store(path)
    contender = _store(path, busy_timeout_ms=30)
    try:
        holder.connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(DurableStoreError) as error:
            contender.append_revision(
                "tenant-a",
                "session-a",
                "run-1",
                request_id="request-revision",
                payload={"x": 1},
                writer_id="writer-a",
                fence_token=1,
                expected_revision=0,
                expected_parent_digest="",
            )
        assert error.value.code is StoreErrorCode.STORE_BUSY
    finally:
        if holder.connection.in_transaction:
            holder.connection.execute("ROLLBACK")
        contender.close()
        holder.close()
