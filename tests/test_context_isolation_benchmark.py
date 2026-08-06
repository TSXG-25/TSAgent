"""Correctness checks for the v2.3A context-isolation Dataset."""

from benchmarks.context_isolation.cases import build_cases
from benchmarks.context_isolation.metadata import benchmark_metadata, dataset_hash
from benchmarks.context_isolation.validate import validate


def test_context_isolation_dataset_is_complete_and_unique() -> None:
    cases = build_cases()

    assert len(cases) == 12
    assert len({case.id for case in cases}) == 12
    assert validate(cases) == []


def test_context_isolation_dataset_hash_is_deterministic() -> None:
    cases = build_cases()

    assert dataset_hash(cases) == dataset_hash(tuple(reversed(tuple(reversed(cases)))))

    metadata = benchmark_metadata(cases)
    assert metadata["case_count"] == 12
    assert metadata["contract_version"] == "adr-0019-v1"
