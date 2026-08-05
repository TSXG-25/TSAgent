"""Reproducible provenance for the v2.2A Checkpoint dataset."""
from __future__ import annotations

import hashlib
import json
from typing import Iterable

from .cases import (
    CHECKPOINT_BENCHMARK_NAME,
    CHECKPOINT_BENCHMARK_VERSION,
    CHECKPOINT_VALIDATOR_VERSION,
    CheckpointCase,
)


def dataset_hash(cases: Iterable[CheckpointCase]) -> str:
    payload = [case.to_dict(include_note=False) for case in cases]
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def benchmark_metadata(cases: Iterable[CheckpointCase]) -> dict:
    cases = list(cases)
    return {
        "benchmark_name": CHECKPOINT_BENCHMARK_NAME,
        "benchmark_version": CHECKPOINT_BENCHMARK_VERSION,
        "validator_version": CHECKPOINT_VALIDATOR_VERSION,
        "dataset_hash": dataset_hash(cases),
        "case_count": len(cases),
    }


__all__ = ["benchmark_metadata", "dataset_hash"]
