"""Immutable return contracts for v2.3B SQLite primitives."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RunHead:
    """The current mutable head for one logical Run."""

    tenant_id: str
    session_id: str
    run_id: str
    request_id: str
    current_revision: int
    current_digest: str
    current_writer_id: str
    current_fence_token: int
    store_generation: str
    run_status: str
    updated_at: str


@dataclass(frozen=True)
class FenceGrant:
    """The writer token currently granted for one Run."""

    tenant_id: str
    session_id: str
    run_id: str
    writer_id: str
    fence_token: int
    fence_epoch: int
    run_revision: int
    store_generation: str
    idempotent: bool = False


@dataclass(frozen=True)
class RevisionRecord:
    """One immutable Run revision appended behind a RunHead CAS."""

    tenant_id: str
    session_id: str
    run_id: str
    revision: int
    parent_digest: str
    payload_json: str
    payload_digest: str
    request_id: str
    writer_id: str
    fence_token: int
    created_at: str


@dataclass(frozen=True)
class PreparedOperation:
    """A durable pre-side-effect intent reserved by ``prepare_operation``."""

    tenant_id: str
    session_id: str
    run_id: str
    operation_id: str
    idempotency_key: str
    operation_type: str
    request_digest: str
    expected_effect_digest: str
    effect_state: str
    external_reference: str
    result_json: str
    result_digest: str
    prepared_revision: int
    committed_revision: int | None
    request_id: str
    fence_epoch: int
    run_revision: int
    store_generation: str
    created_at: str
    updated_at: str


__all__ = [
    "FenceGrant",
    "PreparedOperation",
    "RevisionRecord",
    "RunHead",
]
