"""Contract tests for the pre-implementation v2.3B Store Dataset."""
from __future__ import annotations

from benchmarks.v23b.cases import CrashTrigger, OracleOutcome, build_cases
from benchmarks.v23b.metadata import benchmark_metadata, dataset_hash
from benchmarks.v23b.validate import validate


def test_v23b_dataset_is_complete_and_unique() -> None:
    cases = build_cases()

    assert len(cases) == 19
    assert len({case.id for case in cases}) == 19
    assert validate(cases) == []


def test_v23b_dataset_hash_and_oracle_are_deterministic() -> None:
    cases = build_cases()

    assert dataset_hash(cases) == dataset_hash(tuple(cases))
    metadata = benchmark_metadata(cases)
    assert metadata["benchmark_name"] == "durable-sqlite-runtime-store-v2.3b"
    assert metadata["contract_version"] == "adr-0020-v2"


def test_v23b_has_each_required_crash_boundary() -> None:
    triggers = {case.trigger for case in build_cases()}

    assert {
        CrashTrigger.BEFORE_BEGIN,
        CrashTrigger.PREPARATION_BEFORE_COMMIT,
        CrashTrigger.AFTER_PREPARATION_COMMIT,
        CrashTrigger.AFTER_CHECKPOINT_INSERT,
        CrashTrigger.AFTER_ARTIFACT_METADATA,
        CrashTrigger.AFTER_INDEX_UPDATE,
        CrashTrigger.BEFORE_COMMIT,
        CrashTrigger.AFTER_COMMIT_BEFORE_RESPONSE,
        CrashTrigger.IDEMPOTENCY_SAME_KEY_SAME_DIGEST,
        CrashTrigger.IDEMPOTENCY_SAME_KEY_DIFFERENT_DIGEST,
        CrashTrigger.DIFFERENT_KEY,
        CrashTrigger.FENCE_TAKEOVER,
        CrashTrigger.SIDE_EFFECT_BEFORE_FINALIZATION,
        CrashTrigger.UNKNOWN_EXTERNAL_RESULT,
        CrashTrigger.PROCESS_RESTART_AFTER_COMMIT,
    }.issubset(triggers)
    assert any(
        case.expected_outcome is OracleOutcome.IDEMPOTENT_RETRY
        for case in build_cases()
    )
    assert any(
        case.expected_outcome is OracleOutcome.IDEMPOTENCY_CONFLICT
        for case in build_cases()
    )
