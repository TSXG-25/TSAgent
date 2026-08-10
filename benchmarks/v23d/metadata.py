"""Reproducible metadata for the v2.3D-1 Contract Dataset."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Any

from .cases import (
    BENCHMARK_NAME,
    BENCHMARK_VERSION,
    CONTRACT_VERSION,
    PERFORMANCE_METRICS,
    InterruptionContractCase,
)


def dataset_hash(cases: Iterable[InterruptionContractCase]) -> str:
    payload = sorted(
        (case.to_dict(include_description=False) for case in cases),
        key=lambda value: str(value["id"]),
    )
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def benchmark_metadata(cases: Iterable[InterruptionContractCase]) -> dict[str, Any]:
    values = tuple(cases)
    return {
        "benchmark_name": BENCHMARK_NAME,
        "benchmark_version": BENCHMARK_VERSION,
        "contract_version": CONTRACT_VERSION,
        "case_count": len(values),
        "dataset_hash": dataset_hash(values),
        "performance_metrics": list(PERFORMANCE_METRICS),
        "scope": "Contract/Dataset/Oracle only; no production cancellation",
    }


__all__ = ["benchmark_metadata", "dataset_hash"]
