"""SQLite implementation of the v2.3B durable Runtime Store primitives.

The implementation deliberately contains no Provider, Tool, Workspace or
asyncio calls.  Every write transaction is short, synchronous and uses
``BEGIN IMMEDIATE``.  External side effects belong after a PREPARED intent and
before a later finalization transaction (v2.3B-3).
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

from .contracts import FenceGrant, PreparedOperation, RevisionRecord, RunHead
from .errors import DurableStoreError, StoreErrorCode


SCHEMA_VERSION = "v2.3B-2"
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
                    producer_workflow_id TEXT NOT NULL,
                    producer_stage_id TEXT NOT NULL,
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

                CREATE INDEX IF NOT EXISTS idx_run_revisions_latest
                    ON run_resume_revisions (tenant_id, run_id, revision DESC);
                CREATE INDEX IF NOT EXISTS idx_run_fences_current
                    ON run_fences (tenant_id, run_id, fence_token DESC);
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
