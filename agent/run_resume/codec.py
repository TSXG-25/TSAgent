"""Canonical JSON codec for Run-level resume indexes."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from .contracts import RunResumeIndex


class RunResumeCodecError(ValueError):
    """Raised when a Run-level index cannot be decoded safely."""


def serialize_run_index(index: RunResumeIndex) -> str:
    return json.dumps(
        index.to_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def deserialize_run_index(payload: str | bytes | Mapping[str, Any]) -> RunResumeIndex:
    try:
        value = json.loads(payload) if isinstance(payload, (str, bytes, bytearray)) else dict(payload)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RunResumeCodecError(f"RunResumeIndex JSON 无法解析: {exc}") from exc
    if not isinstance(value, dict):
        raise RunResumeCodecError("RunResumeIndex 顶层必须是 object")
    try:
        return RunResumeIndex.from_dict(value)
    except (TypeError, ValueError, KeyError) as exc:
        raise RunResumeCodecError(f"RunResumeIndex schema 无效: {exc}") from exc


def run_index_digest(index: RunResumeIndex) -> str:
    return hashlib.sha256(serialize_run_index(index).encode("utf-8")).hexdigest()


__all__ = [
    "RunResumeCodecError",
    "deserialize_run_index",
    "run_index_digest",
    "serialize_run_index",
]
