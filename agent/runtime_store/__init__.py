"""Durable SQLite primitives for the v2.3B/v2.3C Runtime Store.

This package owns the durable Runtime Store boundary for v2.3B and v2.3C.  It
provides schema bootstrap, writer fencing, revision CAS, idempotency,
preparation intents, atomic Checkpoint/Artifact/RunIndex finalization,
durable Run events, and the scoped view used by production Runtime contexts.
"""

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
    RunReadSnapshot,
    RunOutputRecord,
    RunHead,
    ServiceStartReservation,
)
from .buffer import CheckpointStagingBuffer
from .errors import DurableStoreError, StoreErrorCode
from .sqlite import (
    DEFAULT_BUSY_TIMEOUT_MS,
    DEFAULT_WAL_AUTOCHECKPOINT,
    SCHEMA_VERSION,
    SqliteRuntimeStore,
)
from .view import (
    DurableRuntimeStoreView,
    SqliteCheckpointStoreAdapter,
    SqliteRunResumeStoreAdapter,
)

__all__ = [
    "DEFAULT_BUSY_TIMEOUT_MS",
    "DEFAULT_WAL_AUTOCHECKPOINT",
    "DurableStoreError",
    "ArtifactCommitFact",
    "DurableEventHead",
    "DurableEventRecord",
    "CheckpointStagingBuffer",
    "FenceGrant",
    "FinalizationBundle",
    "FinalizationFailurePoint",
    "FinalizationResult",
    "PreparedOperation",
    "RevisionRecord",
    "RunReadSnapshot",
    "RunOutputRecord",
    "RunHead",
    "ServiceStartReservation",
    "SCHEMA_VERSION",
    "SqliteRuntimeStore",
    "DurableRuntimeStoreView",
    "SqliteCheckpointStoreAdapter",
    "SqliteRunResumeStoreAdapter",
    "StoreErrorCode",
]
