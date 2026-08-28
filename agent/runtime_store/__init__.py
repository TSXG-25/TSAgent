"""Durable Runtime Store public exports.

The package exposes one public namespace, but importing a small contract or
error must not eagerly import the SQLite implementation and its adapters.
Symbols retain their existing import paths and are loaded on first use.
"""

from __future__ import annotations

import importlib
from typing import Any


_EXPORTS: dict[str, tuple[str, str]] = {
    "ArtifactCommitFact": ("contracts", "ArtifactCommitFact"),
    "DurableEventHead": ("contracts", "DurableEventHead"),
    "DurableEventRecord": ("contracts", "DurableEventRecord"),
    "FenceGrant": ("contracts", "FenceGrant"),
    "FinalizationBundle": ("contracts", "FinalizationBundle"),
    "FinalizationFailurePoint": ("contracts", "FinalizationFailurePoint"),
    "FinalizationResult": ("contracts", "FinalizationResult"),
    "PreparedOperation": ("contracts", "PreparedOperation"),
    "RevisionRecord": ("contracts", "RevisionRecord"),
    "RunReadSnapshot": ("contracts", "RunReadSnapshot"),
    "RunOutputRecord": ("contracts", "RunOutputRecord"),
    "RunHead": ("contracts", "RunHead"),
    "ServiceStartReservation": ("contracts", "ServiceStartReservation"),
    "CheckpointStagingBuffer": ("buffer", "CheckpointStagingBuffer"),
    "DurableStoreError": ("errors", "DurableStoreError"),
    "StoreErrorCode": ("errors", "StoreErrorCode"),
    "DEFAULT_BUSY_TIMEOUT_MS": ("sqlite", "DEFAULT_BUSY_TIMEOUT_MS"),
    "DEFAULT_WAL_AUTOCHECKPOINT": ("sqlite", "DEFAULT_WAL_AUTOCHECKPOINT"),
    "SCHEMA_VERSION": ("sqlite", "SCHEMA_VERSION"),
    "SqliteRuntimeStore": ("sqlite", "SqliteRuntimeStore"),
    "DurableRuntimeStoreView": ("view", "DurableRuntimeStoreView"),
    "SqliteCheckpointStoreAdapter": ("view", "SqliteCheckpointStoreAdapter"),
    "SqliteRunResumeStoreAdapter": ("view", "SqliteRunResumeStoreAdapter"),
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    module_name, attribute = target
    value = getattr(importlib.import_module(f".{module_name}", __name__), attribute)
    globals()[name] = value
    return value
