"""Stable error categories for the durable Runtime Store."""

from __future__ import annotations

from enum import Enum
from typing import Mapping


class StoreErrorCode(str, Enum):
    """Machine-readable Store failures.

    These values are part of the B-2 contract.  SQLite exception text is an
    implementation detail and must not leak into callers as the error model.
    """

    RUN_NOT_FOUND = "RUN_NOT_FOUND"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    REVISION_CONFLICT = "REVISION_CONFLICT"
    PARENT_DIGEST_MISMATCH = "PARENT_DIGEST_MISMATCH"
    STALE_WRITER = "STALE_WRITER"
    STORE_GENERATION_MISMATCH = "STORE_GENERATION_MISMATCH"
    STORE_BUSY = "STORE_BUSY"
    SCHEMA_INCOMPATIBLE = "SCHEMA_INCOMPATIBLE"
    FENCE_CONFLICT = "FENCE_CONFLICT"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    STORE_CLOSED = "STORE_CLOSED"
    INVALID_ARGUMENT = "INVALID_ARGUMENT"


class DurableStoreError(RuntimeError):
    """A deterministic, serializable error from the durable Store."""

    def __init__(
        self,
        code: StoreErrorCode,
        message: str,
        *,
        details: Mapping[str, str] | None = None,
    ) -> None:
        self.code = StoreErrorCode(code)
        self.details = dict(details or {})
        super().__init__(f"{self.code.value}: {message}")


__all__ = ["DurableStoreError", "StoreErrorCode"]
