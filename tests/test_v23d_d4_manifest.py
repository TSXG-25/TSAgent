from __future__ import annotations

from benchmarks.v23d.d4_manifest import (
    ProviderMode,
    build_d4_cases,
    d4_manifest_hash,
    validate_d4_manifest,
)
from agent.service import EventType, RunStatus


def test_d4_manifest_has_stable_d401_to_d410_contract() -> None:
    cases = validate_d4_manifest()

    assert tuple(case.case_id for case in cases) == tuple(
        f"D4{index:02d}" for index in range(1, 11)
    )
    assert cases[0].provider_mode is ProviderMode.REAL_PROVIDER
    assert cases[7].provider_mode is ProviderMode.REAL_PROVIDER
    assert cases[7].expected_terminal_status is RunStatus.TIMED_OUT
    assert cases[7].expected_terminal_event is EventType.RUN_TIMED_OUT
    assert cases[-1].expected_terminal_status is RunStatus.COMPLETED
    assert cases[-1].expected_terminal_event is EventType.RUN_COMPLETED


def test_d4_manifest_hash_is_order_independent_and_round_trips() -> None:
    cases = build_d4_cases()
    assert d4_manifest_hash(cases) == d4_manifest_hash(tuple(reversed(cases)))

    restored = tuple(type(case).from_dict(case.to_dict()) for case in cases)
    assert d4_manifest_hash(restored) == d4_manifest_hash(cases)
