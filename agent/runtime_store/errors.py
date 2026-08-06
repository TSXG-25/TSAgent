"""Stable error categories for the durable Runtime Store."""

from __future__ import annotations

from enum import Enum
from typing import Mapping


class StoreErrorCode(str, Enum):
    """Machine-readable Store failures.

    These values are part of the v2.3B Store contract.  SQLite exception text is an
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
    PREPARED_INTENT_NOT_FOUND = "PREPARED_INTENT_NOT_FOUND"
    EFFECT_STATE_CONFLICT = "EFFECT_STATE_CONFLICT"
    FINALIZATION_CONFLICT = "FINALIZATION_CONFLICT"
    CHECKPOINT_LINEAGE_CONFLICT = "CHECKPOINT_LINEAGE_CONFLICT"
    ARTIFACT_VERIFICATION_FAILED = "ARTIFACT_VERIFICATION_FAILED"
    ARTIFACT_DIGEST_MISMATCH = "ARTIFACT_DIGEST_MISMATCH"
    RUN_INDEX_CONFLICT = "RUN_INDEX_CONFLICT"
    TERMINAL_OUTPUT_MISSING = "TERMINAL_OUTPUT_MISSING"
    FINALIZATION_INJECTED_FAILURE = "FINALIZATION_INJECTED_FAILURE"
    STORE_CLOSED = "STORE_CLOSED"
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    EVENT_CURSOR_EXPIRED = "EVENT_CURSOR_EXPIRED"


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
