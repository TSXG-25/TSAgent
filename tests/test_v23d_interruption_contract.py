"""Deterministic tests for ADR-0023 v2.3D-1 Contract/Dataset/Oracle."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from agent.interruption import (
    AtomicRegion,
    CancellationIntent,
    CancellationSafetyClass,
    CancelRunRequest,
    InterruptionAction,
    InterruptionPhase,
    InterruptionReason,
    InterruptionResultStatus,
    InvalidInterruptionTransition,
    SafeCancellationBoundary,
    allowed_phase_transition,
    can_observe_interruption,
    interruption_policy,
    interruption_policy_contract,
    phase_lifecycle_contract,
    validate_phase_transition,
)
from benchmarks.v23d.cases import (
    D1Group,
    ExpectedOutcome,
    HARD_GATES,
    Probe,
    build_cases,
)
from benchmarks.v23d.metadata import benchmark_metadata, dataset_hash
from benchmarks.v23d.oracle import evaluate
from benchmarks.v23d.validate import validate


def _intent(**overrides: object) -> CancellationIntent:
    values: dict[str, object] = {
        "tenant_id": "tenant-1",
        "user_id": "user-1",
        "session_id": "session-1",
        "run_id": "run-1",
        "request_id": "cancel-1",
        "requested_at": "2026-08-10T00:00:00Z",
        "requested_by": "user-1",
        "reason": InterruptionReason.USER_CANCEL,
        "revision": 3,
        "details": {"origin": "test", "nested": [1, True, None]},
    }
    values.update(overrides)
    return CancellationIntent(**values)  # type: ignore[arg-type]


def test_manifest_freezes_c01_c16_and_all_hard_gates() -> None:
    cases = build_cases()

    assert [case.id for case in cases] == [f"C{index:02d}" for index in range(1, 17)]
    assert len({case.probe for case in cases}) == len(Probe) == 16
    assert {case.group for case in cases} == set(D1Group)
    assert frozenset(gate for case in cases for gate in case.hard_gates) == HARD_GATES
    assert validate(cases) == []


def test_dataset_hash_and_oracle_are_reproducible() -> None:
    cases = build_cases()
    metadata = benchmark_metadata(cases)

    assert dataset_hash(cases) == dataset_hash(tuple(reversed(cases)))
    assert metadata == benchmark_metadata(tuple(cases))
    assert metadata["contract_version"] == "adr-0023-v1"
    assert metadata["case_count"] == 16
    assert len(metadata["dataset_hash"]) == 64
    assert [evaluate(case).to_dict() for case in cases] == [
        evaluate(case).to_dict() for case in cases
    ]


def test_cancel_request_is_identity_complete_and_user_cancel_only() -> None:
    request = CancelRunRequest(
        tenant_id="tenant-1",
        user_id="user-1",
        session_id="session-1",
        run_id="run-1",
        request_id="cancel-1",
        requested_by="user-1",
    )

    assert CancelRunRequest.from_dict(request.to_dict()) == request
    assert request.request_digest == CancelRunRequest.from_dict(
        request.to_dict()
    ).request_digest

    with pytest.raises(ValueError, match="only accepts USER_CANCEL"):
        CancelRunRequest(
            tenant_id="tenant-1",
            user_id="user-1",
            session_id="session-1",
            run_id="run-1",
            request_id="cancel-1",
            requested_by="user-1",
            reason=InterruptionReason.RUN_TIMEOUT,
        )
    with pytest.raises(ValueError, match="tenant_id"):
        CancelRunRequest(
            tenant_id="",
            user_id="user-1",
            session_id="session-1",
            run_id="run-1",
            request_id="cancel-1",
            requested_by="user-1",
        )


def test_durable_intent_roundtrip_digest_and_json_boundary() -> None:
    intent = _intent()
    restored = CancellationIntent.from_dict(intent.to_dict())

    assert restored == intent
    assert restored.intent_digest == intent.intent_digest
    assert json.dumps(intent.to_dict(), sort_keys=True) == json.dumps(
        restored.to_dict(), sort_keys=True
    )

    with pytest.raises(TypeError, match="JSON values only"):
        _intent(details={"live": lambda: None})
    with pytest.raises(ValueError, match="revision"):
        _intent(revision=-1)


def test_reason_policy_is_total_but_reason_specific() -> None:
    contract = interruption_policy_contract()
    assert set(contract) == {reason.value for reason in InterruptionReason}

    user = interruption_policy(InterruptionReason.USER_CANCEL)
    assert user.action is InterruptionAction.CANCEL_AT_SAFE_BOUNDARY
    assert user.resulting_status is InterruptionResultStatus.CANCELLED
    assert not user.resume_allowed

    run_timeout = interruption_policy(InterruptionReason.RUN_TIMEOUT)
    assert run_timeout.action is InterruptionAction.TIME_OUT_AT_SAFE_BOUNDARY
    assert run_timeout.resulting_status is InterruptionResultStatus.TIMED_OUT
    assert not run_timeout.resume_allowed

    shutdown = interruption_policy(InterruptionReason.SERVICE_SHUTDOWN)
    assert shutdown.action is InterruptionAction.SUSPEND_AT_SAFE_BOUNDARY
    assert shutdown.resulting_status is InterruptionResultStatus.SUSPENDED
    assert shutdown.resume_allowed

    for reason in (
        InterruptionReason.STAGE_TIMEOUT,
        InterruptionReason.TOOL_TIMEOUT,
        InterruptionReason.PROVIDER_TIMEOUT,
    ):
        delegated = interruption_policy(reason)
        assert delegated.action is InterruptionAction.DELEGATE_TO_DECISION
        assert delegated.resulting_status is None
        assert delegated.decision_owned


def test_safe_boundary_matrix_never_splits_atomic_regions() -> None:
    for atomic_region in AtomicRegion:
        assert not can_observe_interruption(
            SafeCancellationBoundary.AFTER_FINALIZATION_BUNDLE,
            CancellationSafetyClass.INTERRUPTIBLE,
            atomic_region=atomic_region,
        )

    assert can_observe_interruption(
        SafeCancellationBoundary.DURING_INTERRUPTIBLE_WAIT,
        CancellationSafetyClass.INTERRUPTIBLE,
    )
    assert not can_observe_interruption(
        SafeCancellationBoundary.DURING_INTERRUPTIBLE_WAIT,
        CancellationSafetyClass.BOUNDARY_ONLY,
    )
    assert not can_observe_interruption(
        SafeCancellationBoundary.DURING_INTERRUPTIBLE_WAIT,
        CancellationSafetyClass.NON_CANCELLABLE_ONCE_COMMITTED,
    )
    for boundary in set(SafeCancellationBoundary) - {
        SafeCancellationBoundary.DURING_INTERRUPTIBLE_WAIT
    }:
        assert can_observe_interruption(
            boundary, CancellationSafetyClass.BOUNDARY_ONLY
        )


def test_intent_lifecycle_is_explicit_and_terminal() -> None:
    assert allowed_phase_transition(
        InterruptionPhase.REQUESTED, InterruptionPhase.OBSERVED
    )
    assert allowed_phase_transition(
        InterruptionPhase.OBSERVED, InterruptionPhase.CANCELLING
    )
    assert allowed_phase_transition(
        InterruptionPhase.CANCELLING, InterruptionPhase.FINALIZED
    )
    assert not phase_lifecycle_contract()[InterruptionPhase.FINALIZED.value]

    with pytest.raises(InvalidInterruptionTransition):
        validate_phase_transition(
            InterruptionPhase.REQUESTED, InterruptionPhase.FINALIZED
        )
    with pytest.raises(InvalidInterruptionTransition):
        validate_phase_transition(
            InterruptionPhase.FINALIZED, InterruptionPhase.CANCELLING
        )


def test_oracle_distinguishes_cancel_timeout_delegate_and_rejection() -> None:
    decisions = {case.id: evaluate(case) for case in build_cases()}

    assert decisions["C01"].outcome is ExpectedOutcome.CANCELLED
    assert decisions["C05"].outcome is ExpectedOutcome.CANCELLED
    assert decisions["C06"].outcome is ExpectedOutcome.IDEMPOTENT
    assert decisions["C07"].outcome is ExpectedOutcome.REJECTED
    assert decisions["C11"].outcome is ExpectedOutcome.TIMED_OUT
    assert decisions["C12"].outcome is ExpectedOutcome.DELEGATED
    assert decisions["C15"].outcome is ExpectedOutcome.REJECTED
    assert all(
        not decision.resume_allowed for decision in decisions.values()
    )


def test_contract_package_does_not_import_runtime_implementations() -> None:
    root = Path("agent/interruption")
    forbidden = (
        "agent.runtime",
        "agent.executor",
        "agent.runtime_store",
        "agent.service.service",
        "agent.checkpoint",
    )
    found: list[str] = []
    for path in sorted(root.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            module = ""
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for module in names:
                if module.startswith(forbidden):
                    found.append(f"{path}:{module}")
    assert found == []
