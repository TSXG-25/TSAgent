"""Provenance metadata for the v2.3C Contract Dataset."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Any

from .cases import (
    BENCHMARK_NAME,
    BENCHMARK_VERSION,
    CONTRACT_VERSION,
    ServiceContractCase,
)


def dataset_hash(cases: Iterable[ServiceContractCase]) -> str:
    payload = [case.to_dict(include_description=False) for case in cases]
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def benchmark_metadata(cases: Iterable[ServiceContractCase]) -> dict[str, Any]:
    cases = tuple(cases)
    return {
        "benchmark_name": BENCHMARK_NAME,
        "benchmark_version": BENCHMARK_VERSION,
        "contract_version": CONTRACT_VERSION,
        "case_count": len(cases),
        "dataset_hash": dataset_hash(cases),
    }


__all__ = ["benchmark_metadata", "dataset_hash"]
