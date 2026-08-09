"""Reproducible provenance metadata for the P2 acceptance manifest."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Any

from .cases import BENCHMARK_NAME, BENCHMARK_VERSION, CONTRACT_VERSION, P2Case


def dataset_hash(cases: Iterable[P2Case]) -> str:
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


def benchmark_metadata(cases: Iterable[P2Case]) -> dict[str, Any]:
    cases = tuple(cases)
    return {
        "benchmark_name": BENCHMARK_NAME,
        "benchmark_version": BENCHMARK_VERSION,
        "contract_version": CONTRACT_VERSION,
        "case_count": len(cases),
        "dataset_hash": dataset_hash(cases),
        "scoring": {
            "capability_outcome": ("PASS", "FAIL", "PARTIAL"),
            "runtime_correctness": ("PASS", "FAIL"),
            "hard_gate_zero_required": True,
        },
    }


__all__ = ["benchmark_metadata", "dataset_hash"]
