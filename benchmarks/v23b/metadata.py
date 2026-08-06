"""Provenance metadata for the v2.3B Durable Store Dataset."""
from __future__ import annotations

import hashlib
import json
from typing import Iterable

from .cases import (
    BENCHMARK_NAME,
    BENCHMARK_VERSION,
    CONTRACT_VERSION,
    StoreCrashCase,
)


def dataset_hash(cases: Iterable[StoreCrashCase]) -> str:
    payload = [case.to_dict(include_description=False) for case in cases]
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def benchmark_metadata(cases: Iterable[StoreCrashCase]) -> dict[str, object]:
    cases = tuple(cases)
    return {
        "benchmark_name": BENCHMARK_NAME,
        "benchmark_version": BENCHMARK_VERSION,
        "contract_version": CONTRACT_VERSION,
        "case_count": len(cases),
        "dataset_hash": dataset_hash(cases),
    }


__all__ = ["benchmark_metadata", "dataset_hash"]
