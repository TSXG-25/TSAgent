"""Deterministic v2.3B-2 tests for the SQLite Runtime Store primitives."""

from __future__ import annotations

import concurrent.futures
import io
import json
import sqlite3
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


def test_service_worker_lease_blocks_active_takeover_and_expires_fail_closed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "worker-lease.sqlite"
    store_a = _store(path)
    store_b = _store(path)
    try:
        reservation = store_a.reserve_service_start(
            "tenant-a",
            "session-a",
            requested_run_id="run-worker-lease",
            request_id="service-start-worker-lease",
            request_digest="request-digest",
            writer_id="writer-a",
            external_reference="service-start",
        )
        assert reservation.created is True
        lease = store_a._connection.execute(
            "SELECT writer_id, fence_token, expires_at FROM run_worker_leases"
        ).fetchone()
        assert tuple(lease[:2]) == ("writer-a", 1)
        assert lease["expires_at"]

        with pytest.raises(DurableStoreError) as active:
            store_b.takeover_fence(
                "tenant-a",
                "session-a",
                "run-worker-lease",
                "writer-b",
                expected_fence_token=1,
                worker_lease=True,
            )
        assert active.value.code is StoreErrorCode.FENCE_CONFLICT

        store_a._connection.execute(
            "UPDATE run_worker_leases SET expires_at = '2000-01-01T00:00:00Z'"
        )
        takeover = store_b.takeover_fence(
            "tenant-a",
            "session-a",
            "run-worker-lease",
            "writer-b",
            expected_fence_token=1,
            worker_lease=True,
        )
        assert takeover.fence_token == 2

        with pytest.raises(DurableStoreError) as stale_heartbeat:
            store_a.renew_worker_lease(
                "tenant-a", "session-a", "run-worker-lease", "writer-a", 1
            )
        assert stale_heartbeat.value.code is StoreErrorCode.STALE_WRITER
        store_b.renew_worker_lease(
            "tenant-a", "session-a", "run-worker-lease", "writer-b", 2
        )
        store_b.release_fence(
            "tenant-a", "session-a", "run-worker-lease", "writer-b", 2
        )
        assert store_b._connection.execute(
            "SELECT 1 FROM run_worker_leases WHERE tenant_id = ? AND run_id = ?",
            ("tenant-a", "run-worker-lease"),
        ).fetchone() is None
    finally:
        store_a.close()
        store_b.close()


def test_running_legacy_fence_without_lease_cannot_be_taken_over(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "missing-worker-lease.sqlite")
    try:
        _run_with_fence(store)
        store._connection.execute(
            "UPDATE run_heads SET run_status = 'RUNNING' WHERE run_id = 'run-1'"
        )
        with pytest.raises(DurableStoreError) as missing_lease:
            store.takeover_fence(
                "tenant-a",
                "session-a",
                "run-1",
                "writer-b",
                expected_fence_token=1,
                worker_lease=True,
            )
        assert missing_lease.value.code is StoreErrorCode.FENCE_CONFLICT
    finally:
        store.close()


def test_h3_schema_migrates_worker_lease_table_without_losing_run(tmp_path: Path) -> None:
    path = tmp_path / "worker-lease-migration.sqlite"
    old_store = SqliteRuntimeStore.open(path, schema_version="v2.3H3")
    old_store.initialize_run("tenant-a", "session-a", "run-migration")
    old_store.close()

    connection = sqlite3.connect(path)
    try:
        connection.execute("DROP TABLE run_worker_leases")
        connection.execute(
            "UPDATE runtime_meta SET schema_version = 'v2.3H3' WHERE meta_id = 1"
        )
        connection.commit()
    finally:
        connection.close()

    store = SqliteRuntimeStore.open(path)
    try:
        assert store.schema_version == SCHEMA_VERSION
        assert store.get_run_head("tenant-a", "run-migration") is not None
        assert store._connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
            ("run_worker_leases",),
        ).fetchone() is not None
    finally:
        store.close()


def test_incompatible_store_is_rejected_before_any_ddl(tmp_path: Path) -> None:
    """An unknown schema version must be refused without mutating the
    existing schema — version rejection protects the database itself."""
    path = tmp_path / "foreign-version.sqlite"
    store = SqliteRuntimeStore.open(path)
    store.initialize_run("tenant-a", "session-a", "run-1")
    store.close()

    connection = sqlite3.connect(path)
    try:
        connection.execute("ALTER TABLE run_heads DROP COLUMN task_id")
        connection.execute(
            "UPDATE runtime_meta SET schema_version = 'v9.9-unknown'"
            " WHERE meta_id = 1"
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(DurableStoreError) as incompatible:
        SqliteRuntimeStore.open(path)
    assert incompatible.value.code is StoreErrorCode.SCHEMA_INCOMPATIBLE

    connection = sqlite3.connect(path)
    try:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(run_heads)")
        }
        assert "task_id" not in columns
        version = connection.execute(
            "SELECT schema_version FROM runtime_meta WHERE meta_id = 1"
        ).fetchone()[0]
        assert version == "v9.9-unknown"
    finally:
        connection.close()


def test_generation_mismatch_is_rejected_before_any_ddl(tmp_path: Path) -> None:
    path = tmp_path / "foreign-generation.sqlite"
    store = SqliteRuntimeStore.open(path)
    store.close()

    connection = sqlite3.connect(path)
    try:
        connection.execute("ALTER TABLE run_heads DROP COLUMN task_id")
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(DurableStoreError) as mismatch:
        SqliteRuntimeStore.open(path, expected_store_generation="other-gen")
    assert mismatch.value.code is StoreErrorCode.STORE_GENERATION_MISMATCH

    connection = sqlite3.connect(path)
    try:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(run_heads)")
        }
        assert "task_id" not in columns
    finally:
        connection.close()

    # The supported migration path still applies: the same store opened with
    # its real generation regains the column.
    migrated = SqliteRuntimeStore.open(path)
    try:
        columns = {
            row[1]
            for row in migrated._connection.execute(
                "PRAGMA table_info(run_heads)"
            )
        }
        assert "task_id" in columns
    finally:
        migrated.close()


def test_failed_migration_rolls_back_schema_and_metadata(tmp_path: Path) -> None:
    """A migration failure mid-way must leave the store untouched — schema
    DDL, column migrations and the metadata write commit in one transaction."""
    path = tmp_path / "atomic-migration.sqlite"
    old = SqliteRuntimeStore.open(path, schema_version="v2.3H3")
    old.close()

    connection = sqlite3.connect(path)
    try:
        connection.execute("DROP TABLE run_worker_leases")
        connection.execute(
            """
            CREATE TRIGGER deny_meta_update
            BEFORE UPDATE ON runtime_meta
            BEGIN SELECT RAISE(ABORT, 'deny meta update'); END;
            """
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(DurableStoreError):
        SqliteRuntimeStore.open(path)

    connection = sqlite3.connect(path)
    try:
        assert connection.execute(
            "SELECT name FROM sqlite_master"
            " WHERE type = 'table' AND name = 'run_worker_leases'"
        ).fetchone() is None
        assert connection.execute(
            "SELECT schema_version FROM runtime_meta WHERE meta_id = 1"
        ).fetchone()[0] == "v2.3H3"
    finally:
        connection.close()


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


def test_concurrent_service_starts_serialize_one_tenant_session(
    tmp_path: Path,
) -> None:
    path = tmp_path / "session-starts.sqlite"

    def reserve(index: int) -> tuple[str, str]:
        store = _store(path)
        try:
            try:
                result = store.reserve_service_start(
                    "tenant-a",
                    "session-a",
                    requested_run_id=f"run-{index}",
                    request_id=f"request-{index}",
                    request_digest=f"digest-{index}",
                    writer_id=f"writer-{index}",
                    external_reference=f"service-start:run-{index}",
                )
                return "reserved", result.head.run_id
            except DurableStoreError as error:
                return "error", error.code.value
        finally:
            store.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, (1, 2)))

    assert [result[0] for result in results].count("reserved") == 1
    assert [result[1] for result in results].count(
        StoreErrorCode.SESSION_RUN_ACTIVE.value
    ) == 1

    other_tenant = _store(path)
    try:
        independent = other_tenant.reserve_service_start(
            "tenant-b",
            "session-a",
            requested_run_id="run-other-tenant",
            request_id="request-other-tenant",
            request_digest="digest-other-tenant",
            writer_id="writer-other-tenant",
            external_reference="service-start:run-other-tenant",
        )
        assert independent.created is True
    finally:
        other_tenant.close()


def test_resume_fence_cannot_overlap_another_run_in_the_session(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "session-resume.sqlite")
    try:
        blocked = store.reserve_service_start(
            "tenant-a",
            "session-a",
            requested_run_id="run-blocked",
            request_id="request-blocked",
            request_digest="digest-blocked",
            writer_id="writer-blocked",
            external_reference="service-start:run-blocked",
        )
        store.transition_run_with_event(
            "tenant-a",
            "session-a",
            "run-blocked",
            run_status="RUNNING",
            event_id="run-started:blocked",
            event_type="run_started",
            timestamp="2026-09-27T00:00:00Z",
            payload={"request_id": "request-blocked"},
            writer_id="writer-blocked",
            fence_token=blocked.head.current_fence_token,
            request_id="request-blocked",
            expected_status="CREATED",
        )
        store.transition_run_with_event(
            "tenant-a",
            "session-a",
            "run-blocked",
            run_status="BLOCKED",
            event_id="run-blocked:blocked",
            event_type="run_blocked",
            timestamp="2026-09-27T00:00:01Z",
            payload={"failure_code": "NEEDS_REVIEW"},
            writer_id="writer-blocked",
            fence_token=blocked.head.current_fence_token,
            request_id="request-blocked",
        )
        store.release_fence(
            "tenant-a",
            "session-a",
            "run-blocked",
            "writer-blocked",
            blocked.head.current_fence_token,
            request_id="request-blocked",
        )

        active = store.reserve_service_start(
            "tenant-a",
            "session-a",
            requested_run_id="run-active",
            request_id="request-active",
            request_digest="digest-active",
            writer_id="writer-active",
            external_reference="service-start:run-active",
        )
        with pytest.raises(DurableStoreError) as conflict:
            store.takeover_fence(
                "tenant-a",
                "session-a",
                "run-blocked",
                "writer-resume",
                expected_fence_token=blocked.head.current_fence_token,
                request_id="request-resume",
                worker_lease=True,
            )
        assert conflict.value.code is StoreErrorCode.SESSION_RUN_ACTIVE
        assert conflict.value.details["active_run_id"] == active.head.run_id
    finally:
        store.close()


def test_orphaned_running_run_is_converged_on_next_session_start(
    tmp_path: Path,
) -> None:
    """A RUNNING head whose worker lease expired belongs to a dead worker;
    the next session admission must converge it instead of blocking forever."""
    path = tmp_path / "orphaned-run.sqlite"
    store = _store(path)
    try:
        dead = store.reserve_service_start(
            "tenant-a",
            "session-a",
            requested_run_id="run-dead",
            request_id="request-dead",
            request_digest="digest-dead",
            writer_id="writer-dead",
            external_reference="service-start:run-dead",
        )
        store.transition_run_with_event(
            "tenant-a",
            "session-a",
            "run-dead",
            run_status="RUNNING",
            event_id="run-started:run-dead",
            event_type="run_started",
            timestamp="2026-09-27T00:00:00Z",
            payload={"request_id": "request-dead"},
            writer_id="writer-dead",
            fence_token=dead.head.current_fence_token,
            request_id="request-dead",
            expected_status="CREATED",
        )
        # Worker died: heartbeat stopped and the lease expired.
        store._connection.execute(
            "UPDATE run_worker_leases SET expires_at = '2000-01-01T00:00:00Z'"
        )

        fresh = store.reserve_service_start(
            "tenant-a",
            "session-a",
            requested_run_id="run-fresh",
            request_id="request-fresh",
            request_digest="digest-fresh",
            writer_id="writer-fresh",
            external_reference="service-start:run-fresh",
        )
        assert fresh.created is True

        head = store.get_run_head(
            "tenant-a", "run-dead", session_id="session-a"
        )
        assert str(head.run_status) == "FAILED_TERMINAL"
        event = store._connection.execute(
            "SELECT payload_json FROM run_events"
            " WHERE run_id = 'run-dead' AND event_type = 'run_failed'"
        ).fetchone()
        assert event is not None
        payload = json.loads(event["payload_json"])
        assert payload["failure_code"] == "RUN_WORKER_LOST"
        assert store._connection.execute(
            "SELECT 1 FROM run_worker_leases WHERE run_id = 'run-dead'"
        ).fetchone() is None
    finally:
        store.close()


def test_live_worker_lease_still_blocks_new_run_in_session(
    tmp_path: Path,
) -> None:
    """The orphan sweep must not touch a run whose lease is still alive."""
    store = _store(tmp_path / "live-lease.sqlite")
    try:
        live = store.reserve_service_start(
            "tenant-a",
            "session-a",
            requested_run_id="run-live",
            request_id="request-live",
            request_digest="digest-live",
            writer_id="writer-live",
            external_reference="service-start:run-live",
        )
        store.transition_run_with_event(
            "tenant-a",
            "session-a",
            "run-live",
            run_status="RUNNING",
            event_id="run-started:run-live",
            event_type="run_started",
            timestamp="2026-09-27T00:00:00Z",
            payload={"request_id": "request-live"},
            writer_id="writer-live",
            fence_token=live.head.current_fence_token,
            request_id="request-live",
            expected_status="CREATED",
        )
        with pytest.raises(DurableStoreError) as conflict:
            store.reserve_service_start(
                "tenant-a",
                "session-a",
                requested_run_id="run-new",
                request_id="request-new",
                request_digest="digest-new",
                writer_id="writer-new",
                external_reference="service-start:run-new",
            )
        assert conflict.value.code is StoreErrorCode.SESSION_RUN_ACTIVE
        head = store.get_run_head(
            "tenant-a", "run-live", session_id="session-a"
        )
        assert str(head.run_status) == "RUNNING"
    finally:
        store.close()


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


def test_session_workspace_assignment_round_trips(tmp_path: Path) -> None:
    """The assign write must be readable — _session_contract used to select
    workspace_id and then silently drop it, so list_sessions always showed ""."""
    store = _store(tmp_path / "runtime.sqlite")
    try:
        store.ensure_session("tenant-a", "session-1", "user-a")
        store.ensure_session("tenant-a", "session-2", "user-a")
        store.upsert_workspace("tenant-a", "/tmp/ws-a", display_name="ws-a", path="/tmp/ws-a")

        store.assign_session_workspace("tenant-a", "session-1", "/tmp/ws-a")

        heads = {h.session_id: h for h in store.list_sessions("tenant-a")}
        assert heads["session-1"].workspace_id == "/tmp/ws-a"
        assert heads["session-2"].workspace_id == ""
        assert store.ensure_session("tenant-a", "session-1").workspace_id == "/tmp/ws-a"
        assert store.session_workspace_path("tenant-a", "session-1") == "/tmp/ws-a"
        assert store.workspace_member_ids("tenant-a", "/tmp/ws-a") == ["session-1"]

        store.assign_session_workspace("tenant-a", "session-1", "")
        heads = {h.session_id: h for h in store.list_sessions("tenant-a")}
        assert heads["session-1"].workspace_id == ""

        store.assign_session_workspace("tenant-a", "session-1", "/tmp/ws-a")
        assert store.delete_workspace("tenant-a", "/tmp/ws-a") is True
        heads = {h.session_id: h for h in store.list_sessions("tenant-a")}
        assert heads["session-1"].workspace_id == ""
        assert store.workspace_member_ids("tenant-a", "/tmp/ws-a") == []
    finally:
        store.close()


def test_workspace_source_folders_round_trip(tmp_path: Path) -> None:
    """`paths` carries every source folder while `path` stays the primary."""
    store = _store(tmp_path / "runtime.sqlite")
    try:
        record = store.upsert_workspace(
            "tenant-a",
            "ws-multi",
            display_name="multi",
            path="/a",
            paths_json='["/a", "/b", "/c"]',
        )
        assert record.path == "/a"
        assert record.paths_json == '["/a", "/b", "/c"]'

        listed = {w.workspace_id: w for w in store.list_workspaces("tenant-a")}
        assert listed["ws-multi"].paths_json == '["/a", "/b", "/c"]'

        store.upsert_workspace(
            "tenant-a",
            "ws-multi",
            display_name="multi",
            path="/b",
            paths_json='["/b"]',
        )
        listed = {w.workspace_id: w for w in store.list_workspaces("tenant-a")}
        assert listed["ws-multi"].path == "/b"
        assert listed["ws-multi"].paths_json == '["/b"]'
    finally:
        store.close()


def test_create_run_resolves_bound_session_workspace(tmp_path: Path) -> None:
    """A bound session's runs must scope to the workspace folder even when
    the request carries no explicit workspace_root — the binding lives in
    the store, not in the caller."""
    from types import SimpleNamespace
    from agent.service.context_factory import ServiceContextFactory
    from agent.services.workspace_service import WorkspaceService

    store = _store(tmp_path / "runtime.sqlite")
    try:
        target = tmp_path / "project"
        target.mkdir()
        store.ensure_session("tenant-a", "s-bound", "u1")
        store.ensure_session("tenant-a", "s-bound2", "u1")
        store.upsert_workspace("tenant-a", "ws-dir", display_name="w", path=str(target))
        store.assign_session_workspace("tenant-a", "s-bound", "ws-dir")
        store.assign_session_workspace("tenant-a", "s-bound2", "ws-dir")
        store.ensure_session("tenant-a", "s-free", "u1")

        factory = ServiceContextFactory(store)
        request = lambda sid, root="": SimpleNamespace(
            tenant_id="tenant-a", session_id=sid, user_id="u1",
            request_id="req-x", workspace_root=root,
        )

        ctx = factory.create_run(request("s-bound"), run_id="run-bound")
        assert isinstance(ctx.workspace, WorkspaceService)
        assert Path(ctx.workspace.root) == target
        factory.release_run(ctx)

        ctx = factory.create_run(request("s-bound2", root=str(tmp_path)), run_id="run-explicit")
        assert Path(ctx.workspace.root) == tmp_path
        factory.release_run(ctx)

        ctx = factory.create_run(request("s-free"), run_id="run-free")
        assert ctx.workspace is None
        factory.release_run(ctx)
        factory.close()
    finally:
        store.close()


def test_converge_orphaned_runs_sweeps_all_sessions(tmp_path: Path) -> None:
    """Startup sweep: expired-lease and stale lease-less heads converge to
    FAILED_TERMINAL/RUN_WORKER_LOST; live and just-created runs survive."""
    store = _store(tmp_path / "orphan-sweep.sqlite")
    try:
        # Orphan 1: RUNNING with an expired lease.
        dead = store.reserve_service_start(
            "tenant-a", "session-a", requested_run_id="run-dead",
            request_id="request-dead", request_digest="d1",
            writer_id="writer-dead",
            external_reference="service-start:run-dead",
        )
        store.transition_run_with_event(
            "tenant-a", "session-a", "run-dead",
            run_status="RUNNING", event_id="run-started:run-dead",
            event_type="run_started", timestamp="2026-09-27T00:00:00Z",
            payload={"request_id": "request-dead"},
            writer_id="writer-dead",
            fence_token=dead.head.current_fence_token,
            request_id="request-dead", expected_status="CREATED",
        )
        # Orphan 2: CREATED in another session, no lease, stale updated_at.
        store.reserve_service_start(
            "tenant-a", "session-b", requested_run_id="run-stale",
            request_id="request-stale", request_digest="d2",
            writer_id="writer-stale",
            external_reference="service-start:run-stale",
        )
        # Live: fresh-lease RUNNING + just-created CREATED.
        live = store.reserve_service_start(
            "tenant-a", "session-c", requested_run_id="run-live",
            request_id="request-live", request_digest="d3",
            writer_id="writer-live",
            external_reference="service-start:run-live",
        )
        store.transition_run_with_event(
            "tenant-a", "session-c", "run-live",
            run_status="RUNNING", event_id="run-started:run-live",
            event_type="run_started", timestamp="2026-09-27T00:00:01Z",
            payload={"request_id": "request-live"},
            writer_id="writer-live",
            fence_token=live.head.current_fence_token,
            request_id="request-live", expected_status="CREATED",
        )
        store.reserve_service_start(
            "tenant-a", "session-d", requested_run_id="run-new",
            request_id="request-new", request_digest="d4",
            writer_id="writer-new",
            external_reference="service-start:run-new",
        )
        store._connection.execute(
            "UPDATE run_worker_leases SET expires_at = '2000-01-01T00:00:00Z'"
            " WHERE run_id = 'run-dead'"
        )
        store._connection.execute(
            "UPDATE run_heads SET updated_at = '2000-01-01T00:00:00Z'"
            " WHERE run_id = 'run-stale'"
        )
        # Crash lost the process before/without lease bookkeeping.
        store._connection.execute(
            "DELETE FROM run_worker_leases WHERE run_id = 'run-stale'"
        )

        assert store.converge_orphaned_runs() == 2
        for run_id, session_id in (("run-dead", "session-a"), ("run-stale", "session-b")):
            head = store.get_run_head("tenant-a", run_id, session_id=session_id)
            assert str(head.run_status) == "FAILED_TERMINAL"
            event = store._connection.execute(
                "SELECT payload_json FROM run_events"
                " WHERE run_id = ? AND event_type = 'run_failed'",
                (run_id,),
            ).fetchone()
            assert json.loads(event["payload_json"])["failure_code"] == "RUN_WORKER_LOST"
        for run_id, session_id, status in (
            ("run-live", "session-c", "RUNNING"),
            ("run-new", "session-d", "CREATED"),
        ):
            head = store.get_run_head("tenant-a", run_id, session_id=session_id)
            assert str(head.run_status) == status
    finally:
        store.close()
