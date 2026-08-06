"""Durable SQLite primitives for the v2.3B Runtime Store.

This package owns the durable Runtime Store boundary for v2.3B.  It provides
schema bootstrap, writer fencing, revision CAS, idempotency, preparation
intents, atomic Checkpoint/Artifact/RunIndex finalization, and the scoped view
used by production Runtime contexts.
"""

from .contracts import (
    ArtifactCommitFact,
    FenceGrant,
    FinalizationBundle,
    FinalizationFailurePoint,
    FinalizationResult,
    PreparedOperation,
    RevisionRecord,
    RunReadSnapshot,
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
    "CheckpointStagingBuffer",
    "FenceGrant",
    "FinalizationBundle",
    "FinalizationFailurePoint",
    "FinalizationResult",
    "PreparedOperation",
    "RevisionRecord",
    "RunReadSnapshot",
    "RunHead",
    "ServiceStartReservation",
    "SCHEMA_VERSION",
    "SqliteRuntimeStore",
    "DurableRuntimeStoreView",
    "SqliteCheckpointStoreAdapter",
    "SqliteRunResumeStoreAdapter",
    "StoreErrorCode",
]
