"""Contract checks for the P2 endurance and portability acceptance manifest."""

from benchmarks.p2.cases import P2Group, build_cases
from benchmarks.p2.metadata import benchmark_metadata, dataset_hash
from benchmarks.p2.validate import validate


def test_p2_manifest_has_frozen_group_counts_and_no_oracle_defects() -> None:
    cases = build_cases()

    assert len(cases) == 16
    assert {case.group for case in cases} == set(P2Group)
    assert validate(cases) == []


def test_p2_hash_is_order_independent_and_metadata_is_reproducible() -> None:
    cases = build_cases()
    assert dataset_hash(cases) == dataset_hash(tuple(reversed(cases)))

    first = benchmark_metadata(cases)
    second = benchmark_metadata(tuple(cases))
    assert first == second
    assert first["benchmark_version"] == "v0.1"
    assert first["contract_version"] == "adr-0022-v1"
    assert first["case_count"] == 16
    assert len(first["dataset_hash"]) == 64


def test_provider_cases_are_same_scenario_comparisons_without_reprompt() -> None:
    cases = [case for case in build_cases() if case.group is P2Group.PORTABILITY]

    assert {case.provider_parity_key for case in cases} == {
        "simple-tool", "multi-goal-tool", "unsupported-malformed",
    }
    assert all(case.provider_variants == ("primary", "secondary") for case in cases)
    assert all(not case.reprompt_allowed for case in cases)
