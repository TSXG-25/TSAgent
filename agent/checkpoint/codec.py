"""Deterministic JSON codec for immutable RunCheckpoint snapshots."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from .contracts import RunCheckpoint
from .compatibility import major_version


class CheckpointCodecError(ValueError):
    """Raised when a checkpoint cannot be safely decoded."""


def serialize_checkpoint(checkpoint: RunCheckpoint) -> str:
    """Serialize with stable key ordering for hashing and replay."""
    return json.dumps(
        checkpoint.to_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def deserialize_checkpoint(
    payload: str | bytes | Mapping[str, Any],
    *,
    expected_schema_version: str = "1.0",
) -> RunCheckpoint:
    try:
        if isinstance(payload, (str, bytes, bytearray)):
            value = json.loads(payload)
        else:
            value = dict(payload)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise CheckpointCodecError(f"Checkpoint JSON 无法解析: {exc}") from exc
    if not isinstance(value, dict):
        raise CheckpointCodecError("Checkpoint 顶层必须是 object")
    actual_version = str(value.get("checkpoint_schema_version", ""))
    if major_version(actual_version) != major_version(expected_schema_version):
        raise CheckpointCodecError(
            f"Checkpoint schema major 不兼容: actual={actual_version!r} "
            f"expected={expected_schema_version!r}"
        )
    try:
        return RunCheckpoint.from_dict(value)
    except (TypeError, ValueError, KeyError) as exc:
        raise CheckpointCodecError(f"Checkpoint schema 无效: {exc}") from exc


def checkpoint_digest(checkpoint: RunCheckpoint) -> str:
    return hashlib.sha256(serialize_checkpoint(checkpoint).encode("utf-8")).hexdigest()


__all__ = [
    "CheckpointCodecError",
    "checkpoint_digest",
    "deserialize_checkpoint",
    "serialize_checkpoint",
]
