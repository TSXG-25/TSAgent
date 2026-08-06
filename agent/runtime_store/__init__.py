"""Durable SQLite primitives for the v2.3B Runtime Store.

This package intentionally stops below the Checkpoint/Artifact finalization
bundle.  It provides the durable facts that later runtime layers can compose:
schema bootstrap, writer fencing, revision CAS, idempotency and preparation
intents.
"""

from .contracts import FenceGrant, PreparedOperation, RevisionRecord, RunHead
from .errors import DurableStoreError, StoreErrorCode
from .sqlite import (
    DEFAULT_BUSY_TIMEOUT_MS,
    DEFAULT_WAL_AUTOCHECKPOINT,
    SCHEMA_VERSION,
    SqliteRuntimeStore,
)

__all__ = [
    "DEFAULT_BUSY_TIMEOUT_MS",
    "DEFAULT_WAL_AUTOCHECKPOINT",
    "DurableStoreError",
    "FenceGrant",
    "PreparedOperation",
    "RevisionRecord",
    "RunHead",
    "SCHEMA_VERSION",
    "SqliteRuntimeStore",
    "StoreErrorCode",
]
