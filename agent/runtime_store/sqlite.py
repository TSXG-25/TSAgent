"""SQLite implementation of the v2.3B durable Runtime Store primitives.

The implementation deliberately contains no Provider, Tool, Workspace or
asyncio calls.  Every write transaction is short, synchronous and uses
``BEGIN IMMEDIATE``.  External side effects belong after a PREPARED intent and
before a later Finalization Bundle transaction (v2.3B-3/B-4).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from agent.checkpoint.codec import (
    CheckpointCodecError,
    checkpoint_digest,
    deserialize_checkpoint,
    serialize_checkpoint,
)
from agent.run_resume.codec import (
    RunResumeCodecError,
    deserialize_run_index,
    run_index_digest,
    serialize_run_index,
)

from .contracts import (
    ArtifactCommitFact,
    DurableEventHead,
    DurableEventRecord,
    FenceGrant,
    FinalizationBundle,
    FinalizationFailurePoint,
    FinalizationResult,
    PreparedOperation,
    RevisionRecord,
    RunHead,
    ServiceStartReservation,
    RunReadSnapshot,
)
from .errors import DurableStoreError, StoreErrorCode


SCHEMA_VERSION = "v2.3C-3"
_PREVIOUS_SCHEMA_VERSION = "v2.3B-3"
DEFAULT_BUSY_TIMEOUT_MS = 5_000
DEFAULT_WAL_AUTOCHECKPOINT = 1_000
_MAX_BUSY_TIMEOUT_MS = 60_000
_MAX_WAL_AUTOCHECKPOINT = 100_000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _digest_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_json(value: Any) -> str:
    """Encode only JSON-shaped data and reject process-local live objects."""

    def validate(item: Any, path: str) -> None:
        if item is None or isinstance(item, (str, int, bool)):
            return
        if isinstance(item, float):
            if item != item or item in (float("inf"), float("-inf")):
                raise DurableStoreError(
                    StoreErrorCode.INVALID_ARGUMENT,
                    f"non-finite float at {path}",
                )
            return
        if isinstance(item, Mapping):
            for key, child in item.items():
                if not isinstance(key, str):
                    raise DurableStoreError(
                        StoreErrorCode.INVALID_ARGUMENT,
                        f"JSON object key at {path} must be str",
                    )
                validate(child, f"{path}.{key}")
            return
        if isinstance(item, (list, tuple)):
            for index, child in enumerate(item):
                validate(child, f"{path}[{index}]")
            return
        raise DurableStoreError(
            StoreErrorCode.INVALID_ARGUMENT,
            f"live or non-JSON object at {path}: {type(item).__name__}",
        )

    validate(value, "payload")
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise DurableStoreError(
            StoreErrorCode.INVALID_ARGUMENT,
            "payload is not canonical JSON",
        ) from exc


def _require_text(value: str, label: str) -> str:
    result = str(value).strip()
    if not result:
        raise DurableStoreError(
            StoreErrorCode.INVALID_ARGUMENT,
            f"{label} must be non-empty",
        )
    return result


def _is_busy(error: BaseException) -> bool:
    return isinstance(error, sqlite3.OperationalError) and any(
        marker in str(error).lower()
        for marker in (
            "database is locked",
            "database table is locked",
            "database is busy",
        )
    )


def _busy_error(error: BaseException) -> DurableStoreError:
    return DurableStoreError(
        StoreErrorCode.STORE_BUSY,
        "SQLite writer lock exceeded the bounded busy timeout",
    )


class SqliteRuntimeStore:
    """Single-database durable primitives with one connection per instance.

    A Store instance owns its SQLite connection.  It may be used by threads in
    the same process, but no connection is shared across processes.  Callers
    should create a new instance after process restart with the same database
    path and store generation.
    """

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        path: str,
        schema_version: str,
        store_generation: str,
        busy_timeout_ms: int,
        wal_autocheckpoint: int,
    ) -> None:
        self._connection = connection
        self._lock = threading.RLock()
        self.path = path
        self.schema_version = schema_version
        self.store_generation = store_generation
        self.busy_timeout_ms = busy_timeout_ms
        self.wal_autocheckpoint = wal_autocheckpoint
        self._closed = False

    @classmethod
    def open(
        cls,
        path: str | Path,
        *,
        expected_store_generation: str | None = None,
        schema_version: str = SCHEMA_VERSION,
        busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
        wal_autocheckpoint: int = DEFAULT_WAL_AUTOCHECKPOINT,
    ) -> "SqliteRuntimeStore":
        """Open or bootstrap a file-backed SQLite Runtime Store."""

        if isinstance(path, str) and path == ":memory:":
            raise DurableStoreError(
                StoreErrorCode.INVALID_ARGUMENT,
                "v2.3B requires a file-backed SQLite database",
            )
        if not 1 <= busy_timeout_ms <= _MAX_BUSY_TIMEOUT_MS:
            raise DurableStoreError(
                StoreErrorCode.INVALID_ARGUMENT,
                f"busy_timeout_ms must be between 1 and {_MAX_BUSY_TIMEOUT_MS}",
            )
        if not 1 <= wal_autocheckpoint <= _MAX_WAL_AUTOCHECKPOINT:
            raise DurableStoreError(
                StoreErrorCode.INVALID_ARGUMENT,
                f"wal_autocheckpoint must be between 1 and {_MAX_WAL_AUTOCHECKPOINT}",
            )
        schema_version = _require_text(schema_version, "schema_version")
        expected_generation = (
            _require_text(expected_store_generation, "expected_store_generation")
            if expected_store_generation is not None
            else None
        )

        database_path = Path(path)
        database_path.parent.mkdir(parents=True, exist_ok=True)
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                str(database_path),
                timeout=busy_timeout_ms / 1000.0,
                isolation_level=None,
                check_same_thread=False,
            )
            connection.row_factory = sqlite3.Row
            cls._configure_connection(
                connection,
                busy_timeout_ms=busy_timeout_ms,
                wal_autocheckpoint=wal_autocheckpoint,
            )
            stored_schema, generation = cls._bootstrap_schema(
                connection,
                schema_version=schema_version,
                expected_store_generation=expected_generation,
            )
            return cls(
                connection,
                path=str(database_path),
                schema_version=stored_schema,
                store_generation=generation,
                busy_timeout_ms=busy_timeout_ms,
                wal_autocheckpoint=wal_autocheckpoint,
            )
        except DurableStoreError:
            if connection is not None:
                connection.close()
            raise
        except sqlite3.OperationalError as exc:
            if connection is not None:
                connection.close()
            if _is_busy(exc):
                raise _busy_error(exc) from exc
            raise DurableStoreError(
                StoreErrorCode.SCHEMA_INCOMPATIBLE,
                f"SQLite bootstrap failed: {exc}",
            ) from exc
        except sqlite3.Error as exc:
            if connection is not None:
                connection.close()
            raise DurableStoreError(
                StoreErrorCode.SCHEMA_INCOMPATIBLE,
                f"SQLite bootstrap failed: {exc}",
            ) from exc

    @staticmethod
    def _configure_connection(
        connection: sqlite3.Connection,
        *,
        busy_timeout_ms: int,
        wal_autocheckpoint: int,
    ) -> None:
        journal_mode = str(
            connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
        ).lower()
        if journal_mode != "wal":
            raise DurableStoreError(
                StoreErrorCode.SCHEMA_INCOMPATIBLE,
                f"journal_mode must be WAL, got {journal_mode}",
            )
        connection.execute("PRAGMA synchronous = FULL")
        synchronous = int(connection.execute("PRAGMA synchronous").fetchone()[0])
        if synchronous != 2:
            raise DurableStoreError(
                StoreErrorCode.SCHEMA_INCOMPATIBLE,
                f"synchronous must be FULL(2), got {synchronous}",
            )
        connection.execute("PRAGMA foreign_keys = ON")
        foreign_keys = int(connection.execute("PRAGMA foreign_keys").fetchone()[0])
        if foreign_keys != 1:
            raise DurableStoreError(
                StoreErrorCode.SCHEMA_INCOMPATIBLE,
                "foreign_keys must be ON",
            )
        connection.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")
        actual_busy_timeout = int(
            connection.execute("PRAGMA busy_timeout").fetchone()[0]
        )
        if actual_busy_timeout != busy_timeout_ms:
            raise DurableStoreError(
                StoreErrorCode.SCHEMA_INCOMPATIBLE,
                f"busy_timeout mismatch: {actual_busy_timeout}",
            )
        connection.execute(f"PRAGMA wal_autocheckpoint = {wal_autocheckpoint}")
        actual_autocheckpoint = int(
            connection.execute("PRAGMA wal_autocheckpoint").fetchone()[0]
        )
        if actual_autocheckpoint != wal_autocheckpoint:
            raise DurableStoreError(
                StoreErrorCode.SCHEMA_INCOMPATIBLE,
                f"wal_autocheckpoint mismatch: {actual_autocheckpoint}",
            )

    @staticmethod
    def _bootstrap_schema(
        connection: sqlite3.Connection,
        *,
        schema_version: str,
        expected_store_generation: str | None,
    ) -> tuple[str, str]:
        try:
            # ``executescript`` intentionally runs the DDL in SQLite's own
            # schema transaction.  Start the explicit writer transaction only
            # for the metadata read/insert below; otherwise sqlite3's
            # executescript implicit commit would make our final COMMIT fail.
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runtime_meta (
                    meta_id INTEGER PRIMARY KEY CHECK (meta_id = 1),
                    schema_version TEXT NOT NULL,
                    store_generation TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS run_heads (
                    tenant_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    current_revision INTEGER NOT NULL CHECK (current_revision >= 0),
                    current_digest TEXT NOT NULL,
                    current_writer_id TEXT NOT NULL DEFAULT '',
                    current_fence_token INTEGER NOT NULL CHECK (current_fence_token >= 0),
                    store_generation TEXT NOT NULL,
                    run_status TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, run_id)
                );

                CREATE TABLE IF NOT EXISTS run_resume_revisions (
                    tenant_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    revision INTEGER NOT NULL CHECK (revision > 0),
                    parent_digest TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    payload_digest TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    writer_id TEXT NOT NULL,
                    fence_token INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, run_id, revision),
                    FOREIGN KEY (tenant_id, run_id)
                        REFERENCES run_heads (tenant_id, run_id)
                );

                CREATE TABLE IF NOT EXISTS checkpoints (
                    tenant_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    workflow_id TEXT NOT NULL,
                    checkpoint_id TEXT NOT NULL,
                    sequence_number INTEGER NOT NULL CHECK (sequence_number >= 0),
                    parent_checkpoint_id TEXT,
                    activation_attempt_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    payload_digest TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, run_id, checkpoint_id),
                    UNIQUE (tenant_id, run_id, workflow_id, sequence_number),
                    FOREIGN KEY (tenant_id, run_id)
                        REFERENCES run_heads (tenant_id, run_id)
                );

                CREATE TABLE IF NOT EXISTS artifact_metadata (
                    tenant_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    artifact_id TEXT NOT NULL,
                    artifact_type TEXT NOT NULL,
                    digest TEXT NOT NULL,
                    reference TEXT NOT NULL,
                    exists_flag INTEGER NOT NULL CHECK (exists_flag IN (0, 1)),
                    verified INTEGER NOT NULL CHECK (verified IN (0, 1)),
                    verification_evidence_digest TEXT NOT NULL DEFAULT '',
                    producer_workflow_id TEXT NOT NULL,
                    producer_stage_id TEXT NOT NULL,
                    producer_task_id TEXT NOT NULL DEFAULT '',
                    created_revision INTEGER NOT NULL,
                    last_updated_revision INTEGER NOT NULL,
                    request_id TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, run_id, artifact_id),
                    FOREIGN KEY (tenant_id, run_id)
                        REFERENCES run_heads (tenant_id, run_id)
                );

                CREATE TABLE IF NOT EXISTS idempotency_ledger (
                    tenant_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    operation_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    operation_type TEXT NOT NULL,
                    request_digest TEXT NOT NULL,
                    expected_effect_digest TEXT NOT NULL,
                    effect_state TEXT NOT NULL CHECK (
                        effect_state IN ('PREPARED', 'STARTED', 'COMMITTED', 'FAILED', 'UNKNOWN')
                    ),
                    fence_token INTEGER NOT NULL CHECK (fence_token > 0),
                    external_reference TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    result_digest TEXT NOT NULL,
                    prepared_revision INTEGER NOT NULL,
                    committed_revision INTEGER,
                    request_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, run_id, idempotency_key),
                    UNIQUE (tenant_id, run_id, operation_id),
                    FOREIGN KEY (tenant_id, run_id)
                        REFERENCES run_heads (tenant_id, run_id)
                );

                CREATE TABLE IF NOT EXISTS run_fences (
                    tenant_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    writer_id TEXT NOT NULL,
                    fence_token INTEGER NOT NULL CHECK (fence_token > 0),
                    fence_epoch INTEGER NOT NULL CHECK (fence_epoch > 0),
                    acquired_at TEXT NOT NULL,
                    released_at TEXT,
                    PRIMARY KEY (tenant_id, run_id, fence_token),
                    UNIQUE (tenant_id, run_id, fence_epoch),
                    FOREIGN KEY (tenant_id, run_id)
                        REFERENCES run_heads (tenant_id, run_id)
                );

                CREATE TABLE IF NOT EXISTS run_event_heads (
                    tenant_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    latest_sequence INTEGER NOT NULL CHECK (latest_sequence >= 0),
                    retained_from_sequence INTEGER NOT NULL CHECK (
                        retained_from_sequence >= 0
                    ),
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, run_id),
                    FOREIGN KEY (tenant_id, run_id)
                        REFERENCES run_heads (tenant_id, run_id)
                );

                CREATE TABLE IF NOT EXISTS run_events (
                    tenant_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    sequence_number INTEGER NOT NULL CHECK (sequence_number > 0),
                    event_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    workflow_id TEXT,
                    stage_id TEXT,
                    task_id TEXT,
                    timestamp TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    payload_digest TEXT NOT NULL,
                    run_revision INTEGER NOT NULL CHECK (run_revision >= 0),
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, run_id, sequence_number),
                    UNIQUE (tenant_id, run_id, event_id),
                    FOREIGN KEY (tenant_id, run_id)
                        REFERENCES run_heads (tenant_id, run_id)
                );

                CREATE INDEX IF NOT EXISTS idx_run_revisions_latest
                    ON run_resume_revisions (tenant_id, run_id, revision DESC);
                CREATE INDEX IF NOT EXISTS idx_run_fences_current
                    ON run_fences (tenant_id, run_id, fence_token DESC);
                CREATE INDEX IF NOT EXISTS idx_run_events_replay
                    ON run_events (tenant_id, run_id, sequence_number ASC);
                """
            )
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT schema_version, store_generation
                FROM runtime_meta
                WHERE meta_id = 1
                """
            ).fetchone()
            now = _now()
            if row is None:
                generation = expected_store_generation or uuid.uuid4().hex
                connection.execute(
                    """
                    INSERT INTO runtime_meta
                        (meta_id, schema_version, store_generation, created_at, updated_at)
                    VALUES (1, ?, ?, ?, ?)
                    """,
                    (schema_version, generation, now, now),
                )
                stored_schema = schema_version
            else:
                stored_schema = str(row["schema_version"])
                generation = str(row["store_generation"])
                if stored_schema != schema_version:
                    if (
                        stored_schema == _PREVIOUS_SCHEMA_VERSION
                        and schema_version == SCHEMA_VERSION
                    ):
                        # C-3 only adds tables and indexes.  DDL above is
                        # idempotent, so upgrading the metadata in this
                        # transaction is sufficient and preserves all B data.
                        stored_schema = schema_version
                        connection.execute(
                            """
                            UPDATE runtime_meta
                            SET schema_version = ?, updated_at = ?
                            WHERE meta_id = 1
                            """,
                            (schema_version, now),
                        )
                    else:
                        raise DurableStoreError(
                            StoreErrorCode.SCHEMA_INCOMPATIBLE,
                            f"schema version mismatch: stored={stored_schema} expected={schema_version}",
                        )
                if expected_store_generation is not None and generation != expected_store_generation:
                    raise DurableStoreError(
                        StoreErrorCode.STORE_GENERATION_MISMATCH,
                        "database generation differs from the expected generation",
                    )
                connection.execute(
                    "UPDATE runtime_meta SET updated_at = ? WHERE meta_id = 1",
                    (now,),
                )
            connection.execute("COMMIT")
            return stored_schema, generation
        except DurableStoreError:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        except sqlite3.OperationalError as exc:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            if _is_busy(exc):
                raise _busy_error(exc) from exc
            raise
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def connection(self) -> sqlite3.Connection:
        """Return the owned connection for diagnostics/tests only."""

        self._ensure_open()
        return self._connection

    def pragma_snapshot(self) -> dict[str, int | str]:
        """Return the verified connection settings."""

        with self._lock:
            self._ensure_open()
            return {
                "journal_mode": str(
                    self._connection.execute("PRAGMA journal_mode").fetchone()[0]
                ).lower(),
                "synchronous": int(
                    self._connection.execute("PRAGMA synchronous").fetchone()[0]
                ),
                "foreign_keys": int(
                    self._connection.execute("PRAGMA foreign_keys").fetchone()[0]
                ),
                "busy_timeout": int(
                    self._connection.execute("PRAGMA busy_timeout").fetchone()[0]
                ),
                "wal_autocheckpoint": int(
                    self._connection.execute("PRAGMA wal_autocheckpoint").fetchone()[0]
                ),
            }

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._connection.close()
            self._closed = True

    def __enter__(self) -> "SqliteRuntimeStore":
        self._ensure_open()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def _ensure_open(self) -> None:
        if self._closed:
            raise DurableStoreError(
                StoreErrorCode.STORE_CLOSED,
                "SQLite Runtime Store is closed",
            )

    @contextmanager
    def _write_transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._ensure_open()
            try:
                self._connection.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as exc:
                if _is_busy(exc):
                    raise _busy_error(exc) from exc
                raise
            try:
                yield self._connection
                self._connection.execute("COMMIT")
            except Exception as exc:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                if _is_busy(exc):
                    raise _busy_error(exc) from exc
                if isinstance(exc, sqlite3.IntegrityError):
                    raise DurableStoreError(
                        StoreErrorCode.INVALID_ARGUMENT,
                        "SQLite constraint rejected the Store operation",
                    ) from exc
                raise

    def _check_generation(self, expected_store_generation: str | None) -> None:
        if expected_store_generation is not None and expected_store_generation != self.store_generation:
            raise DurableStoreError(
                StoreErrorCode.STORE_GENERATION_MISMATCH,
                "store generation does not match this database",
            )

    def _fetch_head_tx(
        self,
        connection: sqlite3.Connection,
        tenant_id: str,
        run_id: str,
        *,
        session_id: str | None = None,
    ) -> sqlite3.Row:
        row = connection.execute(
            """
            SELECT tenant_id, session_id, run_id, request_id,
                   current_revision, current_digest, current_writer_id,
                   current_fence_token, store_generation, run_status, updated_at
            FROM run_heads
            WHERE tenant_id = ? AND run_id = ?
            """,
            (tenant_id, run_id),
        ).fetchone()
        if row is None:
            other_scope = connection.execute(
                "SELECT 1 FROM run_heads WHERE run_id = ? LIMIT 1",
                (run_id,),
            ).fetchone()
            if other_scope is not None:
                raise DurableStoreError(
                    StoreErrorCode.IDENTITY_MISMATCH,
                    "run belongs to another tenant scope",
                )
            raise DurableStoreError(
                StoreErrorCode.RUN_NOT_FOUND,
                f"run not found: {run_id}",
            )
        if session_id is not None and str(row["session_id"]) != session_id:
            raise DurableStoreError(
                StoreErrorCode.IDENTITY_MISMATCH,
                "session does not match the Run identity",
            )
        if str(row["store_generation"]) != self.store_generation:
            raise DurableStoreError(
                StoreErrorCode.STORE_GENERATION_MISMATCH,
                "Run head belongs to another store generation",
            )
        return row

    @staticmethod
    def _head_contract(row: sqlite3.Row) -> RunHead:
        return RunHead(
            tenant_id=str(row["tenant_id"]),
            session_id=str(row["session_id"]),
            run_id=str(row["run_id"]),
            request_id=str(row["request_id"]),
            current_revision=int(row["current_revision"]),
            current_digest=str(row["current_digest"]),
            current_writer_id=str(row["current_writer_id"]),
            current_fence_token=int(row["current_fence_token"]),
            store_generation=str(row["store_generation"]),
            run_status=str(row["run_status"]),
            updated_at=str(row["updated_at"]),
        )

    def initialize_run(
        self,
        tenant_id: str,
        session_id: str,
        run_id: str,
        request_id: str = "",
        *,
        run_status: str = "RUNNING",
        expected_store_generation: str | None = None,
    ) -> RunHead:
        tenant_id = _require_text(tenant_id, "tenant_id")
        session_id = _require_text(session_id, "session_id")
        run_id = _require_text(run_id, "run_id")
        run_status = _require_text(run_status, "run_status")
        self._check_generation(expected_store_generation)
        with self._write_transaction() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO run_heads
                        (tenant_id, session_id, run_id, request_id,
                         current_revision, current_digest, current_writer_id,
                         current_fence_token, store_generation, run_status, updated_at)
                    VALUES (?, ?, ?, ?, 0, '', '', 0, ?, ?, ?)
                    """,
                    (
                        tenant_id,
                        session_id,
                        run_id,
                        str(request_id),
                        self.store_generation,
                        run_status,
                        _now(),
                    ),
                )
            except sqlite3.IntegrityError:
                # Re-read inside the same transaction and make duplicate
                # creation idempotent only for the exact Run identity.
                row = self._fetch_head_tx(
                    connection,
                    tenant_id,
                    run_id,
                    session_id=session_id,
                )
                return self._head_contract(row)
            return self._head_contract(
                self._fetch_head_tx(connection, tenant_id, run_id, session_id=session_id)
            )

    def reserve_service_start(
        self,
        tenant_id: str,
        session_id: str,
        *,
        requested_run_id: str | None,
        request_id: str,
        request_digest: str,
        writer_id: str,
        external_reference: str,
        expected_store_generation: str | None = None,
    ) -> ServiceStartReservation:
        """Atomically reserve a public ``start_run`` request.

        The reservation reuses the durable idempotency ledger but performs
        request lookup, Run creation, first fence acquisition, and PREPARED
        intent publication in one ``BEGIN IMMEDIATE`` transaction.  This is
        what makes an omitted client run_id safe under concurrent callers.
        """

        tenant_id = _require_text(tenant_id, "tenant_id")
        session_id = _require_text(session_id, "session_id")
        request_id = _require_text(request_id, "request_id")
        request_digest = _require_text(request_digest, "request_digest")
        writer_id = _require_text(writer_id, "writer_id")
        self._check_generation(expected_store_generation)
        with self._write_transaction() as connection:
            existing = connection.execute(
                """
                SELECT tenant_id, session_id, run_id, operation_id,
                       idempotency_key, operation_type, request_digest,
                       expected_effect_digest, effect_state, fence_token,
                       external_reference, result_json, result_digest,
                       prepared_revision, committed_revision, request_id,
                       created_at, updated_at
                FROM idempotency_ledger
                WHERE tenant_id = ? AND idempotency_key = ?
                ORDER BY prepared_revision ASC
                LIMIT 1
                """,
                (tenant_id, request_id),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["operation_type"]) != "service.start_run"
                    or str(existing["request_digest"]) != request_digest
                ):
                    raise DurableStoreError(
                        StoreErrorCode.IDEMPOTENCY_CONFLICT,
                        "request_id is bound to a different Service operation",
                    )
                if str(existing["session_id"]) != session_id:
                    raise DurableStoreError(
                        StoreErrorCode.IDENTITY_MISMATCH,
                        "request_id belongs to another session scope",
                    )
                head = self._head_contract(
                    self._fetch_head_tx(
                        connection,
                        tenant_id,
                        str(existing["run_id"]),
                        session_id=session_id,
                    )
                )
                return ServiceStartReservation(
                    head=head,
                    intent=self._prepared_contract(
                        existing,
                        store_generation=self.store_generation,
                    ),
                    created=False,
                )

            run_id = _require_text(
                requested_run_id or f"run-{uuid.uuid4().hex}",
                "run_id",
            )
            occupied = connection.execute(
                "SELECT 1 FROM run_heads WHERE tenant_id = ? AND run_id = ?",
                (tenant_id, run_id),
            ).fetchone()
            if occupied is not None:
                raise DurableStoreError(
                    StoreErrorCode.RUN_INDEX_CONFLICT,
                    "requested run_id is already occupied",
                )

            now = _now()
            connection.execute(
                """
                INSERT INTO run_heads
                    (tenant_id, session_id, run_id, request_id,
                     current_revision, current_digest, current_writer_id,
                     current_fence_token, store_generation, run_status, updated_at)
                VALUES (?, ?, ?, ?, 0, '', ?, 1, ?, 'CREATED', ?)
                """,
                (
                    tenant_id,
                    session_id,
                    run_id,
                    request_id,
                    writer_id,
                    self.store_generation,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO run_fences
                    (tenant_id, session_id, run_id, writer_id, fence_token,
                     fence_epoch, acquired_at, released_at)
                VALUES (?, ?, ?, ?, 1, 1, ?, NULL)
                """,
                (tenant_id, session_id, run_id, writer_id, now),
            )

            operation_id = uuid.uuid4().hex
            intent_payload = {
                "effect_state": "PREPARED",
                "expected_effect_digest": "",
                "external_reference": external_reference,
                "idempotency_key": request_id,
                "operation_id": operation_id,
                "operation_type": "service.start_run",
                "request_digest": request_digest,
            }
            payload_json = _canonical_json(intent_payload)
            payload_digest = _digest_text(payload_json)
            connection.execute(
                """
                INSERT INTO idempotency_ledger
                    (tenant_id, session_id, run_id, operation_id,
                     idempotency_key, operation_type, request_digest,
                     expected_effect_digest, effect_state, fence_token,
                     external_reference, result_json, result_digest,
                     prepared_revision, committed_revision, request_id,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'service.start_run', ?, '', 'PREPARED',
                        1, ?, 'null', ?, 1, NULL, ?, ?, ?)
                """,
                (
                    tenant_id,
                    session_id,
                    run_id,
                    operation_id,
                    request_id,
                    request_digest,
                    external_reference,
                    _digest_text("null"),
                    request_id,
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO run_resume_revisions
                    (tenant_id, session_id, run_id, revision, parent_digest,
                     payload_json, payload_digest, request_id, writer_id,
                     fence_token, created_at)
                VALUES (?, ?, ?, 1, '', ?, ?, ?, ?, 1, ?)
                """,
                (
                    tenant_id,
                    session_id,
                    run_id,
                    payload_json,
                    payload_digest,
                    request_id,
                    writer_id,
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE run_heads
                SET current_revision = 1, current_digest = ?, updated_at = ?
                WHERE tenant_id = ? AND run_id = ? AND current_revision = 0
                """,
                (payload_digest, now, tenant_id, run_id),
            )
            row = self._fetch_head_tx(
                connection,
                tenant_id,
                run_id,
                session_id=session_id,
            )
            intent_row = connection.execute(
                """
                SELECT tenant_id, session_id, run_id, operation_id,
                       idempotency_key, operation_type, request_digest,
                       expected_effect_digest, effect_state, fence_token,
                       external_reference, result_json, result_digest,
                       prepared_revision, committed_revision, request_id,
                       created_at, updated_at
                FROM idempotency_ledger
                WHERE tenant_id = ? AND run_id = ? AND idempotency_key = ?
                """,
                (tenant_id, run_id, request_id),
            ).fetchone()
            assert intent_row is not None
            return ServiceStartReservation(
                head=self._head_contract(row),
                intent=self._prepared_contract(
                    intent_row,
                    store_generation=self.store_generation,
                ),
                created=True,
            )

    def get_run_head(
        self,
        tenant_id: str,
        run_id: str,
        *,
        session_id: str | None = None,
    ) -> RunHead | None:
        tenant_id = _require_text(tenant_id, "tenant_id")
        run_id = _require_text(run_id, "run_id")
        if session_id is not None:
            session_id = _require_text(session_id, "session_id")
        with self._lock:
            self._ensure_open()
            row = self._connection.execute(
                """
                SELECT tenant_id, session_id, run_id, request_id,
                       current_revision, current_digest, current_writer_id,
                       current_fence_token, store_generation, run_status, updated_at
                FROM run_heads
                WHERE tenant_id = ? AND run_id = ?
                """,
                (tenant_id, run_id),
            ).fetchone()
            if row is None:
                return None
            if session_id is not None and str(row["session_id"]) != session_id:
                raise DurableStoreError(
                    StoreErrorCode.IDENTITY_MISMATCH,
                    "session does not match the Run identity",
                )
            if str(row["store_generation"]) != self.store_generation:
                raise DurableStoreError(
                    StoreErrorCode.STORE_GENERATION_MISMATCH,
                    "Run head belongs to another store generation",
                )
            return self._head_contract(row)

    def _validate_write_head_tx(
        self,
        connection: sqlite3.Connection,
        tenant_id: str,
        session_id: str,
        run_id: str,
        *,
        writer_id: str,
        fence_token: int,
        expected_revision: int,
        expected_parent_digest: str,
        expected_store_generation: str | None = None,
    ) -> sqlite3.Row:
        self._check_generation(expected_store_generation)
        row = self._fetch_head_tx(
            connection,
            tenant_id,
            run_id,
            session_id=session_id,
        )
        if int(row["current_fence_token"]) != fence_token or str(row["current_writer_id"]) != writer_id:
            raise DurableStoreError(
                StoreErrorCode.STALE_WRITER,
                "writer fence token is no longer current",
            )
        if int(row["current_revision"]) != expected_revision:
            raise DurableStoreError(
                StoreErrorCode.REVISION_CONFLICT,
                f"expected revision {expected_revision}, actual {row['current_revision']}",
            )
        if str(row["current_digest"]) != expected_parent_digest:
            raise DurableStoreError(
                StoreErrorCode.PARENT_DIGEST_MISMATCH,
                "parent digest does not match the current Run head",
            )
        return row

    def get_current_fence(
        self,
        tenant_id: str,
        run_id: str,
        *,
        session_id: str | None = None,
    ) -> FenceGrant | None:
        head = self.get_run_head(tenant_id, run_id, session_id=session_id)
        if head is None:
            raise DurableStoreError(StoreErrorCode.RUN_NOT_FOUND, f"run not found: {run_id}")
        if not head.current_writer_id or head.current_fence_token == 0:
            return None
        return FenceGrant(
            tenant_id=head.tenant_id,
            session_id=head.session_id,
            run_id=head.run_id,
            writer_id=head.current_writer_id,
            fence_token=head.current_fence_token,
            fence_epoch=head.current_fence_token,
            run_revision=head.current_revision,
            store_generation=head.store_generation,
        )

    def acquire_fence(
        self,
        tenant_id: str,
        session_id: str,
        run_id: str,
        writer_id: str,
        *,
        expected_fence_token: int | None = None,
        request_id: str = "",
        expected_store_generation: str | None = None,
    ) -> FenceGrant:
        tenant_id = _require_text(tenant_id, "tenant_id")
        session_id = _require_text(session_id, "session_id")
        run_id = _require_text(run_id, "run_id")
        writer_id = _require_text(writer_id, "writer_id")
        self._check_generation(expected_store_generation)
        with self._write_transaction() as connection:
            row = self._fetch_head_tx(connection, tenant_id, run_id, session_id=session_id)
            current_token = int(row["current_fence_token"])
            current_writer = str(row["current_writer_id"])
            if current_writer == writer_id and current_token > 0:
                if expected_fence_token not in (None, 0, current_token):
                    raise DurableStoreError(
                        StoreErrorCode.STALE_WRITER,
                        "idempotent acquire used a different fence token",
                    )
                return FenceGrant(
                    tenant_id=tenant_id,
                    session_id=session_id,
                    run_id=run_id,
                    writer_id=writer_id,
                    fence_token=current_token,
                    fence_epoch=current_token,
                    run_revision=int(row["current_revision"]),
                    store_generation=self.store_generation,
                    idempotent=True,
                )
            if current_writer:
                raise DurableStoreError(
                    StoreErrorCode.FENCE_CONFLICT,
                    f"Run is already owned by writer {current_writer}",
                )
            if expected_fence_token is not None and expected_fence_token != current_token:
                raise DurableStoreError(
                    StoreErrorCode.STALE_WRITER,
                    "expected fence token does not match the released Run head",
                )
            token = current_token + 1
            self._insert_fence_tx(
                connection,
                tenant_id,
                session_id,
                run_id,
                writer_id,
                token,
                request_id,
            )
            connection.execute(
                """
                UPDATE run_heads
                SET current_writer_id = ?, current_fence_token = ?,
                    request_id = ?, updated_at = ?
                WHERE tenant_id = ? AND run_id = ?
                  AND current_fence_token = ? AND current_writer_id = ''
                """,
                (
                    writer_id,
                    token,
                    str(request_id),
                    _now(),
                    tenant_id,
                    run_id,
                    current_token,
                ),
            )
            return FenceGrant(
                tenant_id=tenant_id,
                session_id=session_id,
                run_id=run_id,
                writer_id=writer_id,
                fence_token=token,
                fence_epoch=token,
                run_revision=int(row["current_revision"]),
                store_generation=self.store_generation,
            )

    def takeover_fence(
        self,
        tenant_id: str,
        session_id: str,
        run_id: str,
        writer_id: str,
        *,
        expected_fence_token: int,
        request_id: str = "",
        expected_store_generation: str | None = None,
    ) -> FenceGrant:
        tenant_id = _require_text(tenant_id, "tenant_id")
        session_id = _require_text(session_id, "session_id")
        run_id = _require_text(run_id, "run_id")
        writer_id = _require_text(writer_id, "writer_id")
        self._check_generation(expected_store_generation)
        with self._write_transaction() as connection:
            row = self._fetch_head_tx(connection, tenant_id, run_id, session_id=session_id)
            current_token = int(row["current_fence_token"])
            current_writer = str(row["current_writer_id"])
            if current_token != expected_fence_token:
                raise DurableStoreError(
                    StoreErrorCode.STALE_WRITER,
                    "takeover expected token is not current",
                )
            if current_writer == writer_id and current_token > 0:
                return FenceGrant(
                    tenant_id=tenant_id,
                    session_id=session_id,
                    run_id=run_id,
                    writer_id=writer_id,
                    fence_token=current_token,
                    fence_epoch=current_token,
                    run_revision=int(row["current_revision"]),
                    store_generation=self.store_generation,
                    idempotent=True,
                )
            token = current_token + 1
            self._insert_fence_tx(
                connection,
                tenant_id,
                session_id,
                run_id,
                writer_id,
                token,
                request_id,
            )
            connection.execute(
                """
                UPDATE run_heads
                SET current_writer_id = ?, current_fence_token = ?,
                    request_id = ?, updated_at = ?
                WHERE tenant_id = ? AND run_id = ?
                  AND current_fence_token = ?
                """,
                (
                    writer_id,
                    token,
                    str(request_id),
                    _now(),
                    tenant_id,
                    run_id,
                    current_token,
                ),
            )
            return FenceGrant(
                tenant_id=tenant_id,
                session_id=session_id,
                run_id=run_id,
                writer_id=writer_id,
                fence_token=token,
                fence_epoch=token,
                run_revision=int(row["current_revision"]),
                store_generation=self.store_generation,
            )

    def _insert_fence_tx(
        self,
        connection: sqlite3.Connection,
        tenant_id: str,
        session_id: str,
        run_id: str,
        writer_id: str,
        token: int,
        request_id: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO run_fences
                (tenant_id, session_id, run_id, writer_id, fence_token,
                 fence_epoch, acquired_at, released_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                tenant_id,
                session_id,
                run_id,
                writer_id,
                token,
                token,
                _now(),
            ),
        )

    def release_fence(
        self,
        tenant_id: str,
        session_id: str,
        run_id: str,
        writer_id: str,
        fence_token: int,
        *,
        request_id: str = "",
        expected_store_generation: str | None = None,
    ) -> None:
        tenant_id = _require_text(tenant_id, "tenant_id")
        session_id = _require_text(session_id, "session_id")
        run_id = _require_text(run_id, "run_id")
        writer_id = _require_text(writer_id, "writer_id")
        self._check_generation(expected_store_generation)
        with self._write_transaction() as connection:
            row = self._fetch_head_tx(connection, tenant_id, run_id, session_id=session_id)
            current_token = int(row["current_fence_token"])
            current_writer = str(row["current_writer_id"])
            if current_writer == "" and current_token == fence_token:
                history = connection.execute(
                    """
                    SELECT writer_id, released_at FROM run_fences
                    WHERE tenant_id = ? AND run_id = ? AND fence_token = ?
                    """,
                    (tenant_id, run_id, fence_token),
                ).fetchone()
                if history is not None and str(history["writer_id"]) == writer_id and history["released_at"] is not None:
                    return
            if current_writer != writer_id or current_token != fence_token:
                raise DurableStoreError(
                    StoreErrorCode.STALE_WRITER,
                    "only the current fence owner may release the Run",
                )
            connection.execute(
                """
                UPDATE run_heads
                SET current_writer_id = '', request_id = ?, updated_at = ?
                WHERE tenant_id = ? AND run_id = ?
                  AND current_writer_id = ? AND current_fence_token = ?
                """,
                (
                    str(request_id),
                    _now(),
                    tenant_id,
                    run_id,
                    writer_id,
                    fence_token,
                ),
            )
            connection.execute(
                """
                UPDATE run_fences
                SET released_at = COALESCE(released_at, ?)
                WHERE tenant_id = ? AND run_id = ? AND fence_token = ?
                """,
                (_now(), tenant_id, run_id, fence_token),
            )

    def append_revision(
        self,
        tenant_id: str,
        session_id: str,
        run_id: str,
        *,
        request_id: str,
        payload: Mapping[str, Any] | Sequence[Any],
        writer_id: str,
        fence_token: int,
        expected_revision: int,
        expected_parent_digest: str,
        run_status: str | None = None,
        expected_store_generation: str | None = None,
    ) -> RevisionRecord:
        tenant_id = _require_text(tenant_id, "tenant_id")
        session_id = _require_text(session_id, "session_id")
        run_id = _require_text(run_id, "run_id")
        writer_id = _require_text(writer_id, "writer_id")
        request_id = _require_text(request_id, "request_id")
        if expected_revision < 0 or fence_token <= 0:
            raise DurableStoreError(
                StoreErrorCode.INVALID_ARGUMENT,
                "expected_revision must be >= 0 and fence_token must be > 0",
            )
        if run_status is not None:
            run_status = _require_text(run_status, "run_status")
        payload_json = _canonical_json(payload)
        payload_digest = _digest_text(payload_json)
        self._check_generation(expected_store_generation)
        with self._write_transaction() as connection:
            row = self._validate_write_head_tx(
                connection,
                tenant_id,
                session_id,
                run_id,
                writer_id=writer_id,
                fence_token=fence_token,
                expected_revision=expected_revision,
                expected_parent_digest=expected_parent_digest,
                expected_store_generation=expected_store_generation,
            )
            revision = int(row["current_revision"]) + 1
            parent_digest = str(row["current_digest"])
            created_at = _now()
            connection.execute(
                """
                INSERT INTO run_resume_revisions
                    (tenant_id, session_id, run_id, revision, parent_digest,
                     payload_json, payload_digest, request_id, writer_id,
                     fence_token, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tenant_id,
                    session_id,
                    run_id,
                    revision,
                    parent_digest,
                    payload_json,
                    payload_digest,
                    request_id,
                    writer_id,
                    fence_token,
                    created_at,
                ),
            )
            new_status = run_status if run_status is not None else str(row["run_status"])
            cursor = connection.execute(
                """
                UPDATE run_heads
                SET current_revision = ?, current_digest = ?,
                    current_writer_id = ?, request_id = ?, run_status = ?,
                    updated_at = ?
                WHERE tenant_id = ? AND run_id = ?
                  AND current_revision = ? AND current_fence_token = ?
                  AND current_writer_id = ?
                """,
                (
                    revision,
                    payload_digest,
                    writer_id,
                    request_id,
                    new_status,
                    created_at,
                    tenant_id,
                    run_id,
                    expected_revision,
                    fence_token,
                    writer_id,
                ),
            )
            if cursor.rowcount != 1:
                raise DurableStoreError(
                    StoreErrorCode.REVISION_CONFLICT,
                    "Run head CAS did not update exactly one row",
                )
            return RevisionRecord(
                tenant_id=tenant_id,
                session_id=session_id,
                run_id=run_id,
                revision=revision,
                parent_digest=parent_digest,
                payload_json=payload_json,
                payload_digest=payload_digest,
                request_id=request_id,
                writer_id=writer_id,
                fence_token=fence_token,
                created_at=created_at,
            )

    def activate_workflow_with_checkpoint(
        self,
        tenant_id: str,
        session_id: str,
        run_id: str,
        workflow_id: str,
        *,
        request_id: str,
        writer_id: str,
        fence_token: int,
        expected_revision: int,
        expected_parent_digest: str,
        initial_checkpoint: Any,
        next_run_index: Any,
        expected_store_generation: str | None = None,
    ) -> Any:
        """Atomically publish pending -> active and its initial Checkpoint."""

        tenant_id = _require_text(tenant_id, "tenant_id")
        session_id = _require_text(session_id, "session_id")
        run_id = _require_text(run_id, "run_id")
        workflow_id = _require_text(workflow_id, "workflow_id")
        request_id = _require_text(request_id, "request_id")
        writer_id = _require_text(writer_id, "writer_id")
        self._check_generation(expected_store_generation)
        with self._write_transaction() as connection:
            head = self._validate_write_head_tx(
                connection,
                tenant_id,
                session_id,
                run_id,
                writer_id=writer_id,
                fence_token=fence_token,
                expected_revision=expected_revision,
                expected_parent_digest=expected_parent_digest,
                expected_store_generation=expected_store_generation,
            )
            if (
                initial_checkpoint.run_id != run_id
                or initial_checkpoint.session_id != session_id
                or initial_checkpoint.workflow_id != workflow_id
                or initial_checkpoint.sequence_number != 0
                or initial_checkpoint.parent_checkpoint_id is not None
                or not initial_checkpoint.activation_attempt_id.strip()
            ):
                raise DurableStoreError(
                    StoreErrorCode.CHECKPOINT_LINEAGE_CONFLICT,
                    "activation checkpoint identity or initial lineage is invalid",
                )
            if initial_checkpoint.status.value != "RUNNING":
                raise DurableStoreError(
                    StoreErrorCode.RUN_INDEX_CONFLICT,
                    "activation checkpoint must start in RUNNING status",
                )
            summary = next_run_index.workflow(workflow_id)
            if summary is None or summary.status.value != "RUNNING":
                raise DurableStoreError(
                    StoreErrorCode.RUN_INDEX_CONFLICT,
                    "activation index must mark the Workflow RUNNING",
                )
            if (
                next_run_index.run_id != run_id
                or next_run_index.session_id != session_id
                or next_run_index.active_workflow_id != workflow_id
                or next_run_index.active_checkpoint_id != initial_checkpoint.checkpoint_id
                or summary.checkpoint_id != initial_checkpoint.checkpoint_id
                or summary.activation_attempt_id != initial_checkpoint.activation_attempt_id
                or next_run_index.revision != expected_revision + 1
                or next_run_index.parent_digest != expected_parent_digest
                or next_run_index.store_generation != self.store_generation
            ):
                raise DurableStoreError(
                    StoreErrorCode.RUN_INDEX_CONFLICT,
                    "activation index does not match the initial Checkpoint",
                )
            checkpoint_json = serialize_checkpoint(initial_checkpoint)
            checkpoint_hash = checkpoint_digest(initial_checkpoint)
            index_json = serialize_run_index(next_run_index)
            index_hash = run_index_digest(next_run_index)
            revision = expected_revision + 1
            now = _now()
            connection.execute(
                """
                INSERT INTO checkpoints
                    (tenant_id, session_id, run_id, workflow_id, checkpoint_id,
                     sequence_number, parent_checkpoint_id, activation_attempt_id,
                     payload_json, payload_digest, request_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?)
                """,
                (
                    tenant_id,
                    session_id,
                    run_id,
                    workflow_id,
                    initial_checkpoint.checkpoint_id,
                    initial_checkpoint.sequence_number,
                    initial_checkpoint.activation_attempt_id,
                    checkpoint_json,
                    checkpoint_hash,
                    request_id,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO run_resume_revisions
                    (tenant_id, session_id, run_id, revision, parent_digest,
                     payload_json, payload_digest, request_id, writer_id,
                     fence_token, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tenant_id,
                    session_id,
                    run_id,
                    revision,
                    expected_parent_digest,
                    index_json,
                    index_hash,
                    request_id,
                    writer_id,
                    fence_token,
                    now,
                ),
            )
            cursor = connection.execute(
                """
                UPDATE run_heads
                SET current_revision = ?, current_digest = ?,
                    current_writer_id = ?, request_id = ?, run_status = 'RUNNING',
                    updated_at = ?
                WHERE tenant_id = ? AND run_id = ?
                  AND current_revision = ? AND current_digest = ?
                  AND current_fence_token = ? AND current_writer_id = ?
                """,
                (
                    revision,
                    index_hash,
                    writer_id,
                    request_id,
                    now,
                    tenant_id,
                    run_id,
                    expected_revision,
                    expected_parent_digest,
                    fence_token,
                    writer_id,
                ),
            )
            if cursor.rowcount != 1:
                raise DurableStoreError(
                    StoreErrorCode.REVISION_CONFLICT,
                    "activation Run Head CAS did not update exactly one row",
                )
            return next_run_index

    def get_latest_revision(
        self,
        tenant_id: str,
        run_id: str,
        *,
        session_id: str | None = None,
    ) -> RevisionRecord | None:
        tenant_id = _require_text(tenant_id, "tenant_id")
        run_id = _require_text(run_id, "run_id")
        with self._lock:
            self._ensure_open()
            if session_id is not None:
                session_id = _require_text(session_id, "session_id")
            row = self._connection.execute(
                """
                SELECT tenant_id, session_id, run_id, revision, parent_digest,
                       payload_json, payload_digest, request_id, writer_id,
                       fence_token, created_at
                FROM run_resume_revisions
                WHERE tenant_id = ? AND run_id = ?
                ORDER BY revision DESC LIMIT 1
                """,
                (tenant_id, run_id),
            ).fetchone()
            if row is None:
                if self.get_run_head(tenant_id, run_id, session_id=session_id) is None:
                    return None
                return None
            if session_id is not None and str(row["session_id"]) != session_id:
                raise DurableStoreError(
                    StoreErrorCode.IDENTITY_MISMATCH,
                    "session does not match the revision identity",
                )
            return self._revision_contract(row)

    def get_checkpoint(
        self,
        tenant_id: str,
        run_id: str,
        checkpoint_id: str,
        *,
        session_id: str | None = None,
    ) -> Any | None:
        """Load one durable Checkpoint without exposing SQLite rows."""

        tenant_id = _require_text(tenant_id, "tenant_id")
        run_id = _require_text(run_id, "run_id")
        checkpoint_id = _require_text(checkpoint_id, "checkpoint_id")
        with self._lock:
            self._ensure_open()
            row = self._connection.execute(
                """
                SELECT tenant_id, session_id, run_id, checkpoint_id,
                       payload_json, payload_digest
                FROM checkpoints
                WHERE tenant_id = ? AND run_id = ? AND checkpoint_id = ?
                """,
                (tenant_id, run_id, checkpoint_id),
            ).fetchone()
            if row is None:
                return None
            if session_id is not None and str(row["session_id"]) != session_id:
                raise DurableStoreError(
                    StoreErrorCode.IDENTITY_MISMATCH,
                    "session does not match the checkpoint identity",
                )
            try:
                checkpoint = deserialize_checkpoint(str(row["payload_json"]))
            except CheckpointCodecError as exc:
                raise DurableStoreError(
                    StoreErrorCode.CHECKPOINT_LINEAGE_CONFLICT,
                    "durable checkpoint payload is not recoverable",
                ) from exc
            if checkpoint_digest(checkpoint) != str(row["payload_digest"]):
                raise DurableStoreError(
                    StoreErrorCode.CHECKPOINT_LINEAGE_CONFLICT,
                    "durable checkpoint digest is inconsistent",
                )
            return checkpoint

    def checkpoint_history(
        self,
        tenant_id: str,
        run_id: str,
        *,
        session_id: str | None = None,
        workflow_id: str | None = None,
        activation_attempt_id: str | None = None,
    ) -> tuple[Any, ...]:
        """Return a decoded immutable Checkpoint lineage for a Run."""

        tenant_id = _require_text(tenant_id, "tenant_id")
        run_id = _require_text(run_id, "run_id")
        clauses = ["tenant_id = ?", "run_id = ?"]
        parameters: list[Any] = [tenant_id, run_id]
        if session_id is not None:
            clauses.append("session_id = ?")
            parameters.append(_require_text(session_id, "session_id"))
        if workflow_id is not None:
            clauses.append("workflow_id = ?")
            parameters.append(_require_text(workflow_id, "workflow_id"))
        if activation_attempt_id is not None:
            clauses.append("activation_attempt_id = ?")
            parameters.append(str(activation_attempt_id))
        query = (
            "SELECT payload_json, payload_digest FROM checkpoints WHERE "
            + " AND ".join(clauses)
            + " ORDER BY created_at ASC, sequence_number ASC, checkpoint_id ASC"
        )
        with self._lock:
            self._ensure_open()
            rows = self._connection.execute(query, tuple(parameters)).fetchall()
            decoded: list[Any] = []
            for row in rows:
                try:
                    checkpoint = deserialize_checkpoint(str(row["payload_json"]))
                except CheckpointCodecError as exc:
                    raise DurableStoreError(
                        StoreErrorCode.CHECKPOINT_LINEAGE_CONFLICT,
                        "durable checkpoint payload is not recoverable",
                    ) from exc
                if checkpoint_digest(checkpoint) != str(row["payload_digest"]):
                    raise DurableStoreError(
                        StoreErrorCode.CHECKPOINT_LINEAGE_CONFLICT,
                        "durable checkpoint digest is inconsistent",
                    )
                decoded.append(checkpoint)
            return tuple(decoded)

    def latest_checkpoint(
        self,
        tenant_id: str,
        run_id: str,
        *,
        session_id: str | None = None,
        workflow_id: str | None = None,
        activation_attempt_id: str | None = None,
    ) -> Any | None:
        history = self.checkpoint_history(
            tenant_id,
            run_id,
            session_id=session_id,
            workflow_id=workflow_id,
            activation_attempt_id=activation_attempt_id,
        )
        return history[-1] if history else None

    def get_run_index(
        self,
        tenant_id: str,
        run_id: str,
        *,
        session_id: str | None = None,
    ) -> Any | None:
        """Decode the latest RunResumeIndex revision.

        Preparation rows share the revision ledger with index rows.  They are
        intentionally skipped here; the index remains the latest valid
        coordination projection until a Finalization Bundle publishes its
        successor.
        """

        tenant_id = _require_text(tenant_id, "tenant_id")
        run_id = _require_text(run_id, "run_id")
        with self._lock:
            self._ensure_open()
            if session_id is not None:
                session_id = _require_text(session_id, "session_id")
            rows = self._connection.execute(
                """
                SELECT session_id, revision, payload_json, payload_digest
                FROM run_resume_revisions
                WHERE tenant_id = ? AND run_id = ?
                ORDER BY revision DESC
                """,
                (tenant_id, run_id),
            ).fetchall()
            for row in rows:
                if session_id is not None and str(row["session_id"]) != session_id:
                    raise DurableStoreError(
                        StoreErrorCode.IDENTITY_MISMATCH,
                        "session does not match the Run index identity",
                    )
                try:
                    index = deserialize_run_index(str(row["payload_json"]))
                except RunResumeCodecError:
                    continue
                if index.run_id != run_id or index.session_id != str(row["session_id"]):
                    raise DurableStoreError(
                        StoreErrorCode.RUN_INDEX_CONFLICT,
                        "durable RunResumeIndex identity is inconsistent",
                    )
                if index.revision != int(row["revision"]):
                    raise DurableStoreError(
                        StoreErrorCode.RUN_INDEX_CONFLICT,
                        "durable RunResumeIndex revision is inconsistent",
                    )
                if run_index_digest(index) != str(row["payload_digest"]):
                    raise DurableStoreError(
                        StoreErrorCode.RUN_INDEX_CONFLICT,
                        "durable RunResumeIndex digest is inconsistent",
                    )
                return index
            return None

    @staticmethod
    def _revision_contract(row: sqlite3.Row) -> RevisionRecord:
        return RevisionRecord(
            tenant_id=str(row["tenant_id"]),
            session_id=str(row["session_id"]),
            run_id=str(row["run_id"]),
            revision=int(row["revision"]),
            parent_digest=str(row["parent_digest"]),
            payload_json=str(row["payload_json"]),
            payload_digest=str(row["payload_digest"]),
            request_id=str(row["request_id"]),
            writer_id=str(row["writer_id"]),
            fence_token=int(row["fence_token"]),
            created_at=str(row["created_at"]),
        )

    def prepare_operation(
        self,
        tenant_id: str,
        session_id: str,
        run_id: str,
        *,
        request_id: str,
        writer_id: str,
        fence_token: int,
        expected_revision: int,
        idempotency_key: str,
        operation_type: str,
        request_digest: str,
        expected_parent_digest: str,
        expected_effect_digest: str = "",
        external_reference: str = "",
        expected_store_generation: str | None = None,
    ) -> PreparedOperation:
        tenant_id = _require_text(tenant_id, "tenant_id")
        session_id = _require_text(session_id, "session_id")
        run_id = _require_text(run_id, "run_id")
        request_id = _require_text(request_id, "request_id")
        writer_id = _require_text(writer_id, "writer_id")
        idempotency_key = _require_text(idempotency_key, "idempotency_key")
        operation_type = _require_text(operation_type, "operation_type")
        request_digest = _require_text(request_digest, "request_digest")
        if fence_token <= 0 or expected_revision < 0:
            raise DurableStoreError(
                StoreErrorCode.INVALID_ARGUMENT,
                "expected_revision must be >= 0 and fence_token must be > 0",
            )
        self._check_generation(expected_store_generation)
        with self._write_transaction() as connection:
            row = self._fetch_head_tx(connection, tenant_id, run_id, session_id=session_id)
            existing = connection.execute(
                """
                SELECT tenant_id, session_id, run_id, operation_id,
                       idempotency_key, operation_type, request_digest,
                       expected_effect_digest, effect_state, fence_token,
                       external_reference,
                       result_json, result_digest, prepared_revision,
                       committed_revision, request_id, created_at, updated_at
                FROM idempotency_ledger
                WHERE tenant_id = ? AND run_id = ? AND idempotency_key = ?
                """,
                (tenant_id, run_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["operation_type"]) != operation_type
                    or str(existing["request_digest"]) != request_digest
                    or str(existing["expected_effect_digest"]) != expected_effect_digest
                ):
                    raise DurableStoreError(
                        StoreErrorCode.IDEMPOTENCY_CONFLICT,
                        "idempotency key is bound to a different operation digest",
                    )
                return self._prepared_contract(
                    existing,
                    store_generation=self.store_generation,
                )

            validated = self._validate_write_head_tx(
                connection,
                tenant_id,
                session_id,
                run_id,
                writer_id=writer_id,
                fence_token=fence_token,
                expected_revision=expected_revision,
                expected_parent_digest=expected_parent_digest,
                expected_store_generation=expected_store_generation,
            )
            revision = int(validated["current_revision"]) + 1
            parent_digest = str(validated["current_digest"])
            operation_id = uuid.uuid4().hex
            now = _now()
            intent_payload = {
                "effect_state": "PREPARED",
                "expected_effect_digest": expected_effect_digest,
                "external_reference": external_reference,
                "idempotency_key": idempotency_key,
                "operation_id": operation_id,
                "operation_type": operation_type,
                "request_digest": request_digest,
            }
            payload_json = _canonical_json(intent_payload)
            payload_digest = _digest_text(payload_json)
            connection.execute(
                """
                INSERT INTO idempotency_ledger
                    (tenant_id, session_id, run_id, operation_id,
                     idempotency_key, operation_type, request_digest,
                     expected_effect_digest, effect_state, fence_token,
                     external_reference,
                    result_json, result_digest, prepared_revision,
                     committed_revision, request_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PREPARED', ?, ?, 'null', ?, ?, NULL, ?, ?, ?)
                """,
                (
                    tenant_id,
                    session_id,
                    run_id,
                    operation_id,
                    idempotency_key,
                    operation_type,
                    request_digest,
                    expected_effect_digest,
                    fence_token,
                    external_reference,
                    _digest_text("null"),
                    revision,
                    request_id,
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO run_resume_revisions
                    (tenant_id, session_id, run_id, revision, parent_digest,
                     payload_json, payload_digest, request_id, writer_id,
                     fence_token, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tenant_id,
                    session_id,
                    run_id,
                    revision,
                    parent_digest,
                    payload_json,
                    payload_digest,
                    request_id,
                    writer_id,
                    fence_token,
                    now,
                ),
            )
            cursor = connection.execute(
                """
                UPDATE run_heads
                SET current_revision = ?, current_digest = ?,
                    current_writer_id = ?, request_id = ?, updated_at = ?
                WHERE tenant_id = ? AND run_id = ?
                  AND current_revision = ? AND current_fence_token = ?
                  AND current_writer_id = ?
                """,
                (
                    revision,
                    payload_digest,
                    writer_id,
                    request_id,
                    now,
                    tenant_id,
                    run_id,
                    expected_revision,
                    fence_token,
                    writer_id,
                ),
            )
            if cursor.rowcount != 1:
                raise DurableStoreError(
                    StoreErrorCode.REVISION_CONFLICT,
                    "Run head CAS did not update exactly one row",
                )
            return PreparedOperation(
                tenant_id=tenant_id,
                session_id=session_id,
                run_id=run_id,
                operation_id=operation_id,
                idempotency_key=idempotency_key,
                operation_type=operation_type,
                request_digest=request_digest,
                expected_effect_digest=expected_effect_digest,
                effect_state="PREPARED",
                external_reference=external_reference,
                result_json="null",
                result_digest=_digest_text("null"),
                prepared_revision=revision,
                committed_revision=None,
                request_id=request_id,
                fence_epoch=fence_token,
                run_revision=revision,
                store_generation=self.store_generation,
                created_at=now,
                updated_at=now,
            )

    def finalize_bundle(
        self,
        bundle: FinalizationBundle,
        *,
        failure_point: FinalizationFailurePoint | str | None = None,
    ) -> FinalizationResult:
        """Atomically commit one verified external result and its projections.

        Verification and all external I/O must happen before this method is
        called.  The method only performs synchronous SQLite work.  A
        committed idempotency record is returned without consulting the stale
        caller revision, which is what makes an after-commit response retry
        safe.
        """

        point: FinalizationFailurePoint | None
        if failure_point is None or failure_point == "":
            point = None
        else:
            try:
                point = FinalizationFailurePoint(failure_point)
            except ValueError as exc:
                raise DurableStoreError(
                    StoreErrorCode.INVALID_ARGUMENT,
                    f"unknown finalization failure point: {failure_point}",
                ) from exc

        tenant_id = _require_text(bundle.tenant_id, "tenant_id")
        session_id = _require_text(bundle.session_id, "session_id")
        run_id = _require_text(bundle.run_id, "run_id")
        workflow_id = _require_text(bundle.workflow_id, "workflow_id")
        request_id = _require_text(bundle.request_id, "request_id")
        writer_id = _require_text(bundle.writer_id, "writer_id")
        idempotency_key = _require_text(bundle.idempotency_key, "idempotency_key")
        operation_type = _require_text(bundle.operation_type, "operation_type")
        request_digest = _require_text(bundle.request_digest, "request_digest")
        external_result_digest = _require_text(
            bundle.external_result_digest,
            "external_result_digest",
        )
        if bundle.fence_epoch <= 0 or bundle.expected_revision < 0:
            raise DurableStoreError(
                StoreErrorCode.INVALID_ARGUMENT,
                "fence_epoch must be > 0 and expected_revision must be >= 0",
            )

        with self._write_transaction() as connection:
            head = self._fetch_head_tx(
                connection,
                tenant_id,
                run_id,
                session_id=session_id,
            )
            intent = connection.execute(
                """
                SELECT tenant_id, session_id, run_id, operation_id,
                       idempotency_key, operation_type, request_digest,
                       expected_effect_digest, effect_state, fence_token,
                       external_reference, result_json, result_digest,
                       prepared_revision, committed_revision, request_id,
                       created_at, updated_at
                FROM idempotency_ledger
                WHERE tenant_id = ? AND run_id = ? AND idempotency_key = ?
                """,
                (tenant_id, run_id, idempotency_key),
            ).fetchone()
            if intent is None:
                raise DurableStoreError(
                    StoreErrorCode.PREPARED_INTENT_NOT_FOUND,
                    "finalization requires a durable Preparation intent",
                )
            if (
                str(intent["operation_type"]) != operation_type
                or str(intent["request_digest"]) != request_digest
            ):
                raise DurableStoreError(
                    StoreErrorCode.IDEMPOTENCY_CONFLICT,
                    "finalization request does not match the Preparation intent",
                )
            effect_state = str(intent["effect_state"])
            if effect_state == "COMMITTED":
                if str(intent["result_digest"]) != external_result_digest:
                    raise DurableStoreError(
                        StoreErrorCode.FINALIZATION_CONFLICT,
                        "same idempotency key has a different final result digest",
                    )
                return self._committed_result_from_intent(
                    intent,
                    store_generation=self.store_generation,
                )
            if effect_state not in {"PREPARED", "STARTED"}:
                raise DurableStoreError(
                    StoreErrorCode.EFFECT_STATE_CONFLICT,
                    f"intent effect state cannot be finalized: {effect_state}",
                )
            expected_effect_digest = str(intent["expected_effect_digest"])
            if expected_effect_digest and expected_effect_digest != external_result_digest:
                raise DurableStoreError(
                    StoreErrorCode.FINALIZATION_CONFLICT,
                    "external result digest differs from the prepared expectation",
                )

            self._validate_finalization_bundle(
                bundle,
                tenant_id=tenant_id,
                session_id=session_id,
                run_id=run_id,
                workflow_id=workflow_id,
                head=head,
            )
            validated = self._validate_write_head_tx(
                connection,
                tenant_id,
                session_id,
                run_id,
                writer_id=writer_id,
                fence_token=bundle.fence_epoch,
                expected_revision=bundle.expected_revision,
                expected_parent_digest=bundle.expected_parent_digest,
            )

            checkpoint = bundle.checkpoint
            index = bundle.next_run_index
            index_json = serialize_run_index(index)
            index_hash = run_index_digest(index)
            revision = int(validated["current_revision"]) + 1
            now = _now()
            for chain_checkpoint in bundle.checkpoint_chain:
                parent_checkpoint_id = self._validate_checkpoint_lineage_tx(
                    connection,
                    tenant_id,
                    run_id,
                    workflow_id,
                    chain_checkpoint,
                )
                connection.execute(
                    """
                    INSERT INTO checkpoints
                        (tenant_id, session_id, run_id, workflow_id, checkpoint_id,
                         sequence_number, parent_checkpoint_id, activation_attempt_id,
                         payload_json, payload_digest, request_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        tenant_id,
                        session_id,
                        run_id,
                        workflow_id,
                        chain_checkpoint.checkpoint_id,
                        chain_checkpoint.sequence_number,
                        parent_checkpoint_id,
                        chain_checkpoint.activation_attempt_id,
                        serialize_checkpoint(chain_checkpoint),
                        checkpoint_digest(chain_checkpoint),
                        request_id,
                        now,
                    ),
                )
            self._maybe_inject_finalization_failure(point, FinalizationFailurePoint.AFTER_CHECKPOINT_INSERT)

            checkpoint_hash = checkpoint_digest(checkpoint)

            for artifact in bundle.artifacts:
                existing_artifact = connection.execute(
                    """
                    SELECT digest, reference, artifact_type, producer_workflow_id,
                           producer_stage_id, producer_task_id, exists_flag, verified
                    FROM artifact_metadata
                    WHERE tenant_id = ? AND run_id = ? AND artifact_id = ?
                    """,
                    (tenant_id, run_id, artifact.artifact_id),
                ).fetchone()
                if existing_artifact is not None and (
                    str(existing_artifact["digest"]) != artifact.digest
                    or str(existing_artifact["reference"]) != artifact.reference
                ):
                    raise DurableStoreError(
                        StoreErrorCode.ARTIFACT_DIGEST_MISMATCH,
                        f"artifact already has a different digest/reference: {artifact.artifact_id}",
                    )
                if existing_artifact is None:
                    connection.execute(
                        """
                        INSERT INTO artifact_metadata
                            (tenant_id, session_id, run_id, artifact_id,
                             artifact_type, digest, reference, exists_flag, verified,
                             verification_evidence_digest, producer_workflow_id,
                             producer_stage_id, producer_task_id, created_revision,
                             last_updated_revision, request_id, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            tenant_id,
                            session_id,
                            run_id,
                            artifact.artifact_id,
                            artifact.artifact_type,
                            artifact.digest,
                            artifact.reference,
                            int(artifact.exists),
                            int(artifact.verified),
                            artifact.verification_evidence_digest,
                            artifact.producer_workflow_id,
                            artifact.producer_stage_id,
                            artifact.producer_task_id,
                            revision,
                            revision,
                            request_id,
                            now,
                        ),
                    )
                else:
                    connection.execute(
                        """
                        UPDATE artifact_metadata
                        SET artifact_type = ?, exists_flag = ?, verified = ?,
                            verification_evidence_digest = ?,
                            producer_workflow_id = ?, producer_stage_id = ?,
                            producer_task_id = ?, last_updated_revision = ?,
                            request_id = ?, updated_at = ?
                        WHERE tenant_id = ? AND run_id = ? AND artifact_id = ?
                        """,
                        (
                            artifact.artifact_type,
                            int(artifact.exists),
                            int(artifact.verified),
                            artifact.verification_evidence_digest,
                            artifact.producer_workflow_id,
                            artifact.producer_stage_id,
                            artifact.producer_task_id,
                            revision,
                            request_id,
                            now,
                            tenant_id,
                            run_id,
                            artifact.artifact_id,
                        ),
                    )
            self._maybe_inject_finalization_failure(point, FinalizationFailurePoint.AFTER_ARTIFACT_METADATA)

            result = FinalizationResult(
                tenant_id=tenant_id,
                session_id=session_id,
                run_id=run_id,
                workflow_id=workflow_id,
                idempotency_key=idempotency_key,
                operation_id=str(intent["operation_id"]),
                effect_state="COMMITTED",
                result_digest=external_result_digest,
                checkpoint_id=checkpoint.checkpoint_id,
                checkpoint_digest=checkpoint_hash,
                run_revision=revision,
                run_index_digest=index_hash,
                artifact_ids=tuple(artifact.artifact_id for artifact in bundle.artifacts),
                committed_at=now,
                store_generation=self.store_generation,
            )
            result_json = _canonical_json(result.to_dict())
            cursor = connection.execute(
                """
                UPDATE idempotency_ledger
                SET effect_state = 'COMMITTED', result_json = ?,
                    result_digest = ?, committed_revision = ?,
                    request_id = ?, updated_at = ?
                WHERE tenant_id = ? AND run_id = ? AND idempotency_key = ?
                  AND effect_state IN ('PREPARED', 'STARTED')
                """,
                (
                    result_json,
                    external_result_digest,
                    revision,
                    request_id,
                    now,
                    tenant_id,
                    run_id,
                    idempotency_key,
                ),
            )
            if cursor.rowcount != 1:
                raise DurableStoreError(
                    StoreErrorCode.EFFECT_STATE_CONFLICT,
                    "Preparation intent changed before finalization update",
                )
            self._maybe_inject_finalization_failure(point, FinalizationFailurePoint.AFTER_LEDGER_UPDATE)

            connection.execute(
                """
                INSERT INTO run_resume_revisions
                    (tenant_id, session_id, run_id, revision, parent_digest,
                     payload_json, payload_digest, request_id, writer_id,
                     fence_token, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tenant_id,
                    session_id,
                    run_id,
                    revision,
                    bundle.expected_parent_digest,
                    index_json,
                    index_hash,
                    request_id,
                    writer_id,
                    bundle.fence_epoch,
                    now,
                ),
            )
            self._maybe_inject_finalization_failure(point, FinalizationFailurePoint.AFTER_INDEX_INSERT)

            run_status = "COMPLETED" if not index.active_workflow_id and not index.pending_workflow_ids else "RUNNING"
            cursor = connection.execute(
                """
                UPDATE run_heads
                SET current_revision = ?, current_digest = ?,
                    current_writer_id = ?, request_id = ?, run_status = ?,
                    updated_at = ?
                WHERE tenant_id = ? AND run_id = ?
                  AND current_revision = ? AND current_digest = ?
                  AND current_fence_token = ? AND current_writer_id = ?
                """,
                (
                    revision,
                    index_hash,
                    writer_id,
                    request_id,
                    run_status,
                    now,
                    tenant_id,
                    run_id,
                    bundle.expected_revision,
                    bundle.expected_parent_digest,
                    bundle.fence_epoch,
                    writer_id,
                ),
            )
            if cursor.rowcount != 1:
                raise DurableStoreError(
                    StoreErrorCode.REVISION_CONFLICT,
                    "finalization Run Head CAS did not update exactly one row",
                )
            self._maybe_inject_finalization_failure(point, FinalizationFailurePoint.BEFORE_COMMIT)
            return result

    def _validate_finalization_bundle(
        self,
        bundle: FinalizationBundle,
        *,
        tenant_id: str,
        session_id: str,
        run_id: str,
        workflow_id: str,
        head: sqlite3.Row,
    ) -> None:
        checkpoint = bundle.checkpoint
        index = bundle.next_run_index
        chain = bundle.checkpoint_chain
        if not chain or chain[-1] != checkpoint:
            raise DurableStoreError(
                StoreErrorCode.CHECKPOINT_LINEAGE_CONFLICT,
                "checkpoint_chain 必须以 final checkpoint 结束",
            )
        for chain_checkpoint in chain:
            if (
                chain_checkpoint.run_id != run_id
                or chain_checkpoint.workflow_id != workflow_id
            ):
                raise DurableStoreError(
                    StoreErrorCode.CHECKPOINT_LINEAGE_CONFLICT,
                    "checkpoint chain Run/Workflow identity does not match the Bundle",
                )
            if chain_checkpoint.session_id != session_id:
                raise DurableStoreError(
                    StoreErrorCode.IDENTITY_MISMATCH,
                    "checkpoint chain session identity does not match the Bundle",
                )
            if not chain_checkpoint.activation_attempt_id.strip():
                raise DurableStoreError(
                    StoreErrorCode.CHECKPOINT_LINEAGE_CONFLICT,
                    "checkpoint chain must identify an activation attempt",
                )
        if any(
            current.activation_attempt_id != chain[0].activation_attempt_id
            for current in chain
        ):
            raise DurableStoreError(
                StoreErrorCode.CHECKPOINT_LINEAGE_CONFLICT,
                "checkpoint chain cannot change activation attempt",
            )
        if checkpoint.run_id != run_id or checkpoint.workflow_id != workflow_id:
            raise DurableStoreError(
                StoreErrorCode.CHECKPOINT_LINEAGE_CONFLICT,
                "checkpoint Run/Workflow identity does not match the Bundle",
            )
        if checkpoint.session_id != session_id:
            raise DurableStoreError(
                StoreErrorCode.IDENTITY_MISMATCH,
                "checkpoint session identity does not match the Bundle",
            )
        if not checkpoint.activation_attempt_id.strip():
            raise DurableStoreError(
                StoreErrorCode.CHECKPOINT_LINEAGE_CONFLICT,
                "finalization checkpoint must identify an activation attempt",
            )
        if bundle.verifier_status.strip().upper() != "VERIFIED" or checkpoint.verifier_status.strip().upper() != "VERIFIED":
            raise DurableStoreError(
                StoreErrorCode.ARTIFACT_VERIFICATION_FAILED,
                "only VERIFIED external evidence can be finalized",
            )
        if len({artifact.artifact_id for artifact in bundle.artifacts}) != len(bundle.artifacts):
            raise DurableStoreError(
                StoreErrorCode.ARTIFACT_VERIFICATION_FAILED,
                "finalization artifact ids must be unique",
            )
        for artifact in bundle.artifacts:
            if artifact.producer_workflow_id != workflow_id:
                raise DurableStoreError(
                    StoreErrorCode.ARTIFACT_VERIFICATION_FAILED,
                    "artifact producer must be the finalized Workflow",
                )
            if not artifact.exists or not artifact.verified or not artifact.digest.strip():
                raise DurableStoreError(
                    StoreErrorCode.ARTIFACT_VERIFICATION_FAILED,
                    f"artifact is not verified: {artifact.artifact_id}",
                )
            if not artifact.verification_evidence_digest.strip():
                raise DurableStoreError(
                    StoreErrorCode.ARTIFACT_VERIFICATION_FAILED,
                    f"artifact evidence digest is missing: {artifact.artifact_id}",
                )
        checkpoint_artifacts = {item.artifact_id: item for item in checkpoint.artifacts}
        for artifact in bundle.artifacts:
            checkpoint_artifact = checkpoint_artifacts.get(artifact.artifact_id)
            if (
                checkpoint_artifact is None
                or checkpoint_artifact.digest != artifact.digest
                or checkpoint_artifact.reference != artifact.reference
                or not checkpoint_artifact.exists
            ):
                raise DurableStoreError(
                    StoreErrorCode.ARTIFACT_DIGEST_MISMATCH,
                    f"Checkpoint artifact differs from commit fact: {artifact.artifact_id}",
                )
        if index.run_id != run_id or index.session_id != session_id:
            raise DurableStoreError(
                StoreErrorCode.RUN_INDEX_CONFLICT,
                "RunResumeIndex identity does not match the Bundle",
            )
        if index.store_generation != self.store_generation:
            raise DurableStoreError(
                StoreErrorCode.STORE_GENERATION_MISMATCH,
                "RunResumeIndex belongs to another store generation",
            )
        if index.revision != bundle.expected_revision + 1:
            raise DurableStoreError(
                StoreErrorCode.RUN_INDEX_CONFLICT,
                "RunResumeIndex revision must be the next Run revision",
            )
        if index.parent_digest != bundle.expected_parent_digest:
            raise DurableStoreError(
                StoreErrorCode.RUN_INDEX_CONFLICT,
                "RunResumeIndex parent digest does not match the Run Head",
            )
        summary = index.workflow(workflow_id)
        if summary is None:
            raise DurableStoreError(
                StoreErrorCode.RUN_INDEX_CONFLICT,
                "finalized Workflow is absent from RunResumeIndex",
            )
        if checkpoint.workflow_version != summary.workflow_version:
            raise DurableStoreError(
                StoreErrorCode.CHECKPOINT_LINEAGE_CONFLICT,
                "checkpoint workflow version differs from RunResumeIndex",
            )
        if summary.activation_attempt_id != checkpoint.activation_attempt_id:
            raise DurableStoreError(
                StoreErrorCode.CHECKPOINT_LINEAGE_CONFLICT,
                "Checkpoint activation attempt differs from RunResumeIndex",
            )
        index_artifacts = {item.artifact_id: item for item in index.artifacts}
        for artifact in bundle.artifacts:
            projected = index_artifacts.get(artifact.artifact_id)
            if (
                projected is None
                or projected.digest != artifact.digest
                or projected.reference != artifact.reference
                or not projected.exists
                or not projected.verified
            ):
                raise DurableStoreError(
                    StoreErrorCode.RUN_INDEX_CONFLICT,
                    f"RunResumeIndex artifact projection differs: {artifact.artifact_id}",
                )
        terminal = summary.status.value == "COMPLETED" or workflow_id in index.completed_workflow_ids
        if terminal:
            if checkpoint.status.value != "COMPLETED":
                raise DurableStoreError(
                    StoreErrorCode.RUN_INDEX_CONFLICT,
                    "completed Workflow requires a COMPLETED checkpoint",
                )
            if index.active_workflow_id or index.active_checkpoint_id:
                raise DurableStoreError(
                    StoreErrorCode.RUN_INDEX_CONFLICT,
                    "completed Workflow cannot remain active in the next Run index",
                )
            if summary.checkpoint_id != checkpoint.checkpoint_id:
                raise DurableStoreError(
                    StoreErrorCode.RUN_INDEX_CONFLICT,
                    "completed Workflow must point at the finalized checkpoint",
                )
            if not bundle.artifacts:
                raise DurableStoreError(
                    StoreErrorCode.TERMINAL_OUTPUT_MISSING,
                    "completed Workflow requires at least one verified terminal artifact",
                )
        else:
            if index.active_workflow_id != workflow_id or index.active_checkpoint_id != checkpoint.checkpoint_id:
                raise DurableStoreError(
                    StoreErrorCode.RUN_INDEX_CONFLICT,
                    "active Run index must point to the finalized checkpoint",
                )
            if summary.checkpoint_id != checkpoint.checkpoint_id:
                raise DurableStoreError(
                    StoreErrorCode.RUN_INDEX_CONFLICT,
                    "active Workflow must point at the finalized checkpoint",
                )
            if checkpoint.status.value == "COMPLETED":
                raise DurableStoreError(
                    StoreErrorCode.RUN_INDEX_CONFLICT,
                    "a COMPLETED checkpoint must complete its Workflow projection",
                )
        if int(head["current_revision"]) != bundle.expected_revision or str(head["current_digest"]) != bundle.expected_parent_digest:
            raise DurableStoreError(
                StoreErrorCode.REVISION_CONFLICT,
                "finalization Bundle parent does not match the current Run Head",
            )

    @staticmethod
    def _validate_checkpoint_lineage_tx(
        connection: sqlite3.Connection,
        tenant_id: str,
        run_id: str,
        workflow_id: str,
        checkpoint: Any,
    ) -> str | None:
        latest = connection.execute(
            """
            SELECT checkpoint_id, sequence_number, activation_attempt_id
            FROM checkpoints
            WHERE tenant_id = ? AND run_id = ? AND workflow_id = ?
            ORDER BY sequence_number DESC
            LIMIT 1
            """,
            (tenant_id, run_id, workflow_id),
        ).fetchone()
        if latest is None:
            if checkpoint.sequence_number != 0 or checkpoint.parent_checkpoint_id is not None:
                raise DurableStoreError(
                    StoreErrorCode.CHECKPOINT_LINEAGE_CONFLICT,
                    "first checkpoint must have sequence=0 and no parent",
                )
            return None
        if str(latest["activation_attempt_id"]) != checkpoint.activation_attempt_id:
            raise DurableStoreError(
                StoreErrorCode.CHECKPOINT_LINEAGE_CONFLICT,
                "checkpoint activation attempt changed within one Workflow chain",
            )
        expected_sequence = int(latest["sequence_number"]) + 1
        if checkpoint.sequence_number != expected_sequence or checkpoint.parent_checkpoint_id != str(latest["checkpoint_id"]):
            raise DurableStoreError(
                StoreErrorCode.CHECKPOINT_LINEAGE_CONFLICT,
                "checkpoint sequence/parent does not extend the latest chain",
            )
        return str(latest["checkpoint_id"])

    @staticmethod
    def _maybe_inject_finalization_failure(
        requested: FinalizationFailurePoint | None,
        point: FinalizationFailurePoint,
    ) -> None:
        if requested is point:
            raise DurableStoreError(
                StoreErrorCode.FINALIZATION_INJECTED_FAILURE,
                f"deterministic failure injected at {point.value}",
            )

    @staticmethod
    def _committed_result_from_intent(
        row: sqlite3.Row,
        *,
        store_generation: str,
    ) -> FinalizationResult:
        try:
            payload = json.loads(str(row["result_json"]))
            if not isinstance(payload, dict):
                raise ValueError("finalization result must be an object")
            result = FinalizationResult.from_dict(payload, idempotent=True)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise DurableStoreError(
                StoreErrorCode.FINALIZATION_CONFLICT,
                "committed finalization result is not recoverable JSON",
            ) from exc
        if result.result_digest != str(row["result_digest"]):
            raise DurableStoreError(
                StoreErrorCode.FINALIZATION_CONFLICT,
                "committed finalization result digest is inconsistent",
            )
        if result.store_generation != store_generation:
            raise DurableStoreError(
                StoreErrorCode.STORE_GENERATION_MISMATCH,
                "committed finalization belongs to another store generation",
            )
        return result

    def get_idempotency(
        self,
        tenant_id: str,
        run_id: str,
        idempotency_key: str,
        *,
        session_id: str | None = None,
    ) -> PreparedOperation | None:
        tenant_id = _require_text(tenant_id, "tenant_id")
        run_id = _require_text(run_id, "run_id")
        idempotency_key = _require_text(idempotency_key, "idempotency_key")
        with self._lock:
            self._ensure_open()
            if session_id is not None:
                session_id = _require_text(session_id, "session_id")
            row = self._connection.execute(
                """
                SELECT tenant_id, session_id, run_id, operation_id,
                       idempotency_key, operation_type, request_digest,
                       expected_effect_digest, effect_state, fence_token,
                       external_reference,
                       result_json, result_digest, prepared_revision,
                       committed_revision, request_id, created_at, updated_at
                FROM idempotency_ledger
                WHERE tenant_id = ? AND run_id = ? AND idempotency_key = ?
                """,
                (tenant_id, run_id, idempotency_key),
            ).fetchone()
            if row is None:
                return None
            if session_id is not None and str(row["session_id"]) != session_id:
                raise DurableStoreError(
                    StoreErrorCode.IDENTITY_MISMATCH,
                    "session does not match the idempotency identity",
                )
            self._fetch_head_tx(
                self._connection,
                tenant_id,
                run_id,
                session_id=str(row["session_id"]),
            )
            return self._prepared_contract(
                row,
                store_generation=self.store_generation,
            )

    def read_run_snapshot(
        self,
        tenant_id: str,
        run_id: str,
        *,
        session_id: str | None = None,
    ) -> RunReadSnapshot:
        """Read Service projection inputs from one SQLite read transaction."""

        tenant_id = _require_text(tenant_id, "tenant_id")
        run_id = _require_text(run_id, "run_id")
        if session_id is not None:
            session_id = _require_text(session_id, "session_id")
        with self._lock:
            self._ensure_open()
            try:
                self._connection.execute("BEGIN")
                head = self._head_contract(
                    self._fetch_head_tx(
                        self._connection,
                        tenant_id,
                        run_id,
                        session_id=session_id,
                    )
                )
                index = self.get_run_index(
                    tenant_id,
                    run_id,
                    session_id=session_id,
                )
                intent_row = self._connection.execute(
                    """
                    SELECT tenant_id, session_id, run_id, operation_id,
                           idempotency_key, operation_type, request_digest,
                           expected_effect_digest, effect_state, fence_token,
                           external_reference, result_json, result_digest,
                           prepared_revision, committed_revision, request_id,
                           created_at, updated_at
                    FROM idempotency_ledger
                    WHERE tenant_id = ? AND run_id = ?
                      AND operation_type = 'service.start_run'
                    ORDER BY prepared_revision ASC
                    LIMIT 1
                    """,
                    (tenant_id, run_id),
                ).fetchone()
                start_intent = (
                    self._prepared_contract(
                        intent_row,
                        store_generation=self.store_generation,
                    )
                    if intent_row is not None
                    else None
                )
                self._connection.execute("COMMIT")
                return RunReadSnapshot(
                    head=head,
                    index=index,
                    start_intent=start_intent,
                )
            except Exception:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise

    @staticmethod
    def _event_contract(row: sqlite3.Row) -> DurableEventRecord:
        return DurableEventRecord(
            event_id=str(row["event_id"]),
            sequence_number=int(row["sequence_number"]),
            tenant_id=str(row["tenant_id"]),
            session_id=str(row["session_id"]),
            run_id=str(row["run_id"]),
            workflow_id=(
                None if row["workflow_id"] is None else str(row["workflow_id"])
            ),
            stage_id=None if row["stage_id"] is None else str(row["stage_id"]),
            task_id=None if row["task_id"] is None else str(row["task_id"]),
            event_type=str(row["event_type"]),
            timestamp=str(row["timestamp"]),
            payload_json=str(row["payload_json"]),
            payload_digest=str(row["payload_digest"]),
            run_revision=int(row["run_revision"]),
        )

    @staticmethod
    def _event_columns() -> str:
        return (
            "tenant_id, session_id, run_id, sequence_number, event_id, "
            "event_type, workflow_id, stage_id, task_id, timestamp, "
            "payload_json, payload_digest, run_revision, created_at"
        )

    def append_event(
        self,
        tenant_id: str,
        session_id: str,
        run_id: str,
        *,
        event_id: str,
        event_type: str,
        timestamp: str,
        payload: Mapping[str, Any],
        workflow_id: str | None = None,
        stage_id: str | None = None,
        task_id: str | None = None,
        run_revision: int = 0,
        expected_store_generation: str | None = None,
    ) -> DurableEventRecord:
        """Append one event with per-Run sequence and event-id idempotency.

        Sequence allocation and the event insert occur inside the same short
        ``BEGIN IMMEDIATE`` transaction.  No caller-provided sequence is
        accepted, so concurrent connections cannot race on ``MAX + 1``.
        """

        tenant_id = _require_text(tenant_id, "tenant_id")
        session_id = _require_text(session_id, "session_id")
        run_id = _require_text(run_id, "run_id")
        event_id = _require_text(event_id, "event_id")
        event_type_value = getattr(event_type, "value", event_type)
        event_type_value = _require_text(str(event_type_value), "event_type")
        timestamp = _require_text(timestamp, "timestamp")
        if isinstance(run_revision, bool) or run_revision < 0:
            raise DurableStoreError(
                StoreErrorCode.INVALID_ARGUMENT,
                "run_revision must be a non-negative integer",
            )
        if not isinstance(payload, Mapping):
            raise DurableStoreError(
                StoreErrorCode.INVALID_ARGUMENT,
                "event payload must be a JSON object",
            )
        optional_ids = {
            "workflow_id": None if workflow_id is None else _require_text(workflow_id, "workflow_id"),
            "stage_id": None if stage_id is None else _require_text(stage_id, "stage_id"),
            "task_id": None if task_id is None else _require_text(task_id, "task_id"),
        }
        payload_json = _canonical_json(payload)
        payload_digest = _digest_text(payload_json)
        self._check_generation(expected_store_generation)

        with self._write_transaction() as connection:
            self._fetch_head_tx(
                connection,
                tenant_id,
                run_id,
                session_id=session_id,
            )
            existing = connection.execute(
                f"SELECT {self._event_columns()} FROM run_events "
                "WHERE tenant_id = ? AND run_id = ? AND event_id = ?",
                (tenant_id, run_id, event_id),
            ).fetchone()
            if existing is not None:
                same_identity = (
                    str(existing["session_id"]) == session_id
                    and str(existing["event_type"]) == event_type_value
                    and existing["workflow_id"] == optional_ids["workflow_id"]
                    and existing["stage_id"] == optional_ids["stage_id"]
                    and existing["task_id"] == optional_ids["task_id"]
                    and str(existing["timestamp"]) == timestamp
                    and str(existing["payload_digest"]) == payload_digest
                    and int(existing["run_revision"]) == run_revision
                )
                if not same_identity:
                    raise DurableStoreError(
                        StoreErrorCode.IDEMPOTENCY_CONFLICT,
                        "event_id is bound to a different event payload",
                    )
                return self._event_contract(existing)

            latest = connection.execute(
                "SELECT event_type FROM run_events "
                "WHERE tenant_id = ? AND run_id = ? "
                "ORDER BY sequence_number DESC LIMIT 1",
                (tenant_id, run_id),
            ).fetchone()
            if latest is not None and str(latest["event_type"]) in {
                "run_completed",
                "run_failed",
                "run_blocked",
            }:
                raise DurableStoreError(
                    StoreErrorCode.INVALID_ARGUMENT,
                    "a terminal Run event cannot be followed by another event",
                )

            now = _now()
            connection.execute(
                """
                INSERT OR IGNORE INTO run_event_heads
                    (tenant_id, session_id, run_id, latest_sequence,
                     retained_from_sequence, updated_at)
                VALUES (?, ?, ?, 0, 0, ?)
                """,
                (tenant_id, session_id, run_id, now),
            )
            event_head = connection.execute(
                """
                SELECT latest_sequence
                FROM run_event_heads
                WHERE tenant_id = ? AND run_id = ?
                """,
                (tenant_id, run_id),
            ).fetchone()
            if event_head is None:
                raise DurableStoreError(
                    StoreErrorCode.INVALID_ARGUMENT,
                    "event head could not be initialized",
                )
            sequence = int(event_head["latest_sequence"]) + 1
            connection.execute(
                """
                INSERT INTO run_events
                    (tenant_id, session_id, run_id, sequence_number, event_id,
                     event_type, workflow_id, stage_id, task_id, timestamp,
                     payload_json, payload_digest, run_revision, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tenant_id,
                    session_id,
                    run_id,
                    sequence,
                    event_id,
                    event_type_value,
                    optional_ids["workflow_id"],
                    optional_ids["stage_id"],
                    optional_ids["task_id"],
                    timestamp,
                    payload_json,
                    payload_digest,
                    run_revision,
                    now,
                ),
            )
            cursor = connection.execute(
                """
                UPDATE run_event_heads
                SET latest_sequence = ?, updated_at = ?
                WHERE tenant_id = ? AND run_id = ?
                  AND latest_sequence = ?
                """,
                (sequence, now, tenant_id, run_id, sequence - 1),
            )
            if cursor.rowcount != 1:
                raise DurableStoreError(
                    StoreErrorCode.REVISION_CONFLICT,
                    "event head CAS did not update exactly one row",
                )
            row = connection.execute(
                f"SELECT {self._event_columns()} FROM run_events "
                "WHERE tenant_id = ? AND run_id = ? AND sequence_number = ?",
                (tenant_id, run_id, sequence),
            ).fetchone()
            if row is None:
                raise DurableStoreError(
                    StoreErrorCode.INVALID_ARGUMENT,
                    "newly appended event could not be reloaded",
                )
            return self._event_contract(row)

    def get_event_head(
        self,
        tenant_id: str,
        run_id: str,
        *,
        session_id: str | None = None,
    ) -> DurableEventHead:
        """Read cursor and terminal metadata from one SQLite snapshot."""

        tenant_id = _require_text(tenant_id, "tenant_id")
        run_id = _require_text(run_id, "run_id")
        if session_id is not None:
            session_id = _require_text(session_id, "session_id")
        with self._lock:
            self._ensure_open()
            try:
                self._connection.execute("BEGIN")
                head = self._fetch_head_tx(
                    self._connection,
                    tenant_id,
                    run_id,
                    session_id=session_id,
                )
                row = self._connection.execute(
                    """
                    SELECT latest_sequence, retained_from_sequence
                    FROM run_event_heads
                    WHERE tenant_id = ? AND run_id = ?
                    """,
                    (tenant_id, run_id),
                ).fetchone()
                latest_sequence = int(row["latest_sequence"]) if row is not None else 0
                retained_from_sequence = int(row["retained_from_sequence"]) if row is not None else 0
                terminal = self._connection.execute(
                    """
                    SELECT sequence_number
                    FROM run_events
                    WHERE tenant_id = ? AND run_id = ?
                      AND event_type IN ('run_completed', 'run_failed', 'run_blocked')
                    ORDER BY sequence_number ASC
                    LIMIT 1
                    """,
                    (tenant_id, run_id),
                ).fetchone()
                self._connection.execute("COMMIT")
                return DurableEventHead(
                    tenant_id=tenant_id,
                    session_id=str(head["session_id"]),
                    run_id=run_id,
                    latest_sequence=latest_sequence,
                    retained_from_sequence=retained_from_sequence,
                    terminal_sequence=(
                        None if terminal is None else int(terminal["sequence_number"])
                    ),
                )
            except Exception:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise

    def read_events(
        self,
        tenant_id: str,
        run_id: str,
        *,
        session_id: str,
        after_sequence: int = 0,
        limit: int | None = None,
    ) -> tuple[DurableEventRecord, ...]:
        """Read events using an exclusive, per-Run cursor."""

        tenant_id = _require_text(tenant_id, "tenant_id")
        session_id = _require_text(session_id, "session_id")
        run_id = _require_text(run_id, "run_id")
        if isinstance(after_sequence, bool) or after_sequence < 0:
            raise DurableStoreError(
                StoreErrorCode.INVALID_ARGUMENT,
                "after_sequence must be a non-negative integer",
            )
        if limit is not None and (isinstance(limit, bool) or limit < 1):
            raise DurableStoreError(
                StoreErrorCode.INVALID_ARGUMENT,
                "limit must be a positive integer",
            )
        with self._lock:
            self._ensure_open()
            try:
                self._connection.execute("BEGIN")
                self._fetch_head_tx(
                    self._connection,
                    tenant_id,
                    run_id,
                    session_id=session_id,
                )
                event_head = self._connection.execute(
                    """
                    SELECT latest_sequence, retained_from_sequence
                    FROM run_event_heads
                    WHERE tenant_id = ? AND run_id = ?
                    """,
                    (tenant_id, run_id),
                ).fetchone()
                latest_sequence = int(event_head["latest_sequence"]) if event_head is not None else 0
                retained_from_sequence = int(event_head["retained_from_sequence"]) if event_head is not None else 0
                if after_sequence < retained_from_sequence:
                    raise DurableStoreError(
                        StoreErrorCode.EVENT_CURSOR_EXPIRED,
                        "event cursor is older than the retained event floor",
                    )
                if after_sequence > latest_sequence:
                    rows: list[sqlite3.Row] = []
                else:
                    limit_sql = "" if limit is None else " LIMIT ?"
                    params: tuple[Any, ...]
                    if limit is None:
                        params = (tenant_id, run_id, after_sequence)
                    else:
                        params = (tenant_id, run_id, after_sequence, limit)
                    rows = list(
                        self._connection.execute(
                            f"SELECT {self._event_columns()} FROM run_events "
                            "WHERE tenant_id = ? AND run_id = ? "
                            "AND sequence_number > ? "
                            "ORDER BY sequence_number ASC" + limit_sql,
                            params,
                        ).fetchall()
                    )
                self._connection.execute("COMMIT")
                return tuple(self._event_contract(row) for row in rows)
            except Exception:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise

    def set_event_retention_floor(
        self,
        tenant_id: str,
        session_id: str,
        run_id: str,
        retained_from_sequence: int,
    ) -> None:
        """Set a test/maintenance cursor floor without deleting event rows."""

        tenant_id = _require_text(tenant_id, "tenant_id")
        session_id = _require_text(session_id, "session_id")
        run_id = _require_text(run_id, "run_id")
        if isinstance(retained_from_sequence, bool) or retained_from_sequence < 0:
            raise DurableStoreError(
                StoreErrorCode.INVALID_ARGUMENT,
                "retained_from_sequence must be non-negative",
            )
        with self._write_transaction() as connection:
            self._fetch_head_tx(connection, tenant_id, run_id, session_id=session_id)
            row = connection.execute(
                """
                SELECT latest_sequence, retained_from_sequence
                FROM run_event_heads
                WHERE tenant_id = ? AND run_id = ?
                """,
                (tenant_id, run_id),
            ).fetchone()
            latest_sequence = int(row["latest_sequence"]) if row is not None else 0
            previous_floor = int(row["retained_from_sequence"]) if row is not None else 0
            if retained_from_sequence > latest_sequence or retained_from_sequence < previous_floor:
                raise DurableStoreError(
                    StoreErrorCode.INVALID_ARGUMENT,
                    "retention floor must be monotonic and not exceed latest sequence",
                )
            now = _now()
            connection.execute(
                """
                INSERT INTO run_event_heads
                    (tenant_id, session_id, run_id, latest_sequence,
                     retained_from_sequence, updated_at)
                VALUES (?, ?, ?, 0, ?, ?)
                ON CONFLICT (tenant_id, run_id) DO UPDATE SET
                    retained_from_sequence = excluded.retained_from_sequence,
                    updated_at = excluded.updated_at
                """,
                (tenant_id, session_id, run_id, retained_from_sequence, now),
            )

    @staticmethod
    def _prepared_contract(
        row: sqlite3.Row,
        *,
        store_generation: str,
    ) -> PreparedOperation:
        committed = row["committed_revision"]
        prepared_revision = int(row["prepared_revision"])
        return PreparedOperation(
            tenant_id=str(row["tenant_id"]),
            session_id=str(row["session_id"]),
            run_id=str(row["run_id"]),
            operation_id=str(row["operation_id"]),
            idempotency_key=str(row["idempotency_key"]),
            operation_type=str(row["operation_type"]),
            request_digest=str(row["request_digest"]),
            expected_effect_digest=str(row["expected_effect_digest"]),
            effect_state=str(row["effect_state"]),
            external_reference=str(row["external_reference"]),
            result_json=str(row["result_json"]),
            result_digest=str(row["result_digest"]),
            prepared_revision=prepared_revision,
            committed_revision=int(committed) if committed is not None else None,
            request_id=str(row["request_id"]),
            fence_epoch=int(row["fence_token"]),
            run_revision=int(committed) if committed is not None else prepared_revision,
            store_generation=store_generation,
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )


__all__ = [
    "DEFAULT_BUSY_TIMEOUT_MS",
    "DEFAULT_WAL_AUTOCHECKPOINT",
    "SCHEMA_VERSION",
    "SqliteRuntimeStore",
]
