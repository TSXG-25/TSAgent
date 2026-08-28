"""Stable provenance metadata for the Memory Fuzz dataset."""
from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path
from typing import Iterable

from benchmarks.memory.cases import MemoryCase


BENCHMARK_NAME = "memory-fuzz"
BENCHMARK_VERSION = "v0.3"
DATASET_VERSION = "v0.1"
VALIDATOR_VERSION = "adr-0014-v2"


def _case_payload(case: MemoryCase) -> dict:
    """Return deterministic evaluation fields used by the dataset hash."""
    payload = dataclasses.asdict(case)
    # Notes are documentation, not evaluation input.
    payload.pop("note", None)
    return payload


def dataset_hash(cases: Iterable[MemoryCase]) -> str:
    payload = [_case_payload(case) for case in cases]
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def fixture_manifest_hash(cases: Iterable[MemoryCase]) -> str:
    """Hash declared fixture contents so fixture edits change provenance."""
    root = Path(__file__).resolve().parents[2]
    manifest = []
    for case in cases:
        source_name = str(getattr(case, "fixture_source", "") or "").strip()
        if not source_name:
            continue
        source = (root / source_name).resolve()
        try:
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
        except OSError:
            digest = "MISSING"
        manifest.append({"case_id": case.id, "source": source_name, "sha256": digest})
    encoded = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def benchmark_metadata(
    cases: Iterable[MemoryCase],
    *,
    benchmark_name: str = BENCHMARK_NAME,
    benchmark_version: str = BENCHMARK_VERSION,
) -> dict:
    cases = list(cases)
    return {
        "benchmark_name": benchmark_name,
        "benchmark_version": benchmark_version,
        "dataset_version": DATASET_VERSION,
        "validator_version": VALIDATOR_VERSION,
        "dataset_hash": dataset_hash(cases),
        "fixture_manifest_hash": fixture_manifest_hash(cases),
        "case_count": len(cases),
    }
