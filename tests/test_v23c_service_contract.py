from __future__ import annotations

import json

import pytest

from agent.service import (
    AgentServiceError,
    ArtifactSummary,
    EventOrderingOracle,
    EventStreamRequest,
    EventType,
    ExternalStateEvidence,
    NoEffectConfirmation,
    ResumeAction,
    ResumeDisposition,
    ResumeRunRequest,
    ResumeSummary,
    RunEvent,
    RunHandle,
    RunSnapshot,
    RunStatus,
    ServiceErrorCode,
    StartRunRequest,
)
from benchmarks.v23c.cases import build_cases
from benchmarks.v23c.metadata import benchmark_metadata
from benchmarks.v23c.oracle import evaluate
from benchmarks.v23c.validate import validate


def _event(sequence_number: int, event_type: EventType) -> RunEvent:
    return RunEvent(
        event_id=f"event-{sequence_number}",
        sequence_number=sequence_number,
        tenant_id="tenant-1",
        session_id="session-1",
        run_id="run-1",
        workflow_id="workflow-a",
        stage_id=None,
        task_id=None,
        event_type=event_type,
        timestamp=f"2026-08-06T00:00:0{sequence_number}Z",
        payload={"nested": {"sequence": sequence_number}},
        run_revision=sequence_number,
    )


def test_waiting_user_event_is_nonterminal_and_can_be_followed_by_resume() -> None:
    events = (
        _event(1, EventType.RUN_STARTED),
        _event(2, EventType.RUN_WAITING_USER),
        _event(3, EventType.RUN_RESUMED),
    )

    assert not events[1].is_terminal
    assert EventOrderingOracle.validate(events) == events


def test_public_dtos_round_trip_and_digest_are_stable() -> None:
    request = StartRunRequest(
        tenant_id="tenant-1",
        user_id="user-1",
        session_id="session-1",
        run_id="run-1",
        request_id="request-1",
        request_text="生成报告",
        metadata={"emoji": "✅", "tags": ["one", "two"]},
    )
    handle = RunHandle(
        tenant_id="tenant-1",
        session_id="session-1",
        run_id="run-1",
        request_id="request-1",
        status=RunStatus.RUNNING,
        revision=2,
        created_at="2026-08-06T00:00:00Z",
    )
    event = _event(1, EventType.RUN_STARTED)

    assert StartRunRequest.from_dict(request.to_dict()) == request
    assert RunHandle.from_dict(handle.to_dict()) == handle
    assert RunEvent.from_dict(event.to_dict()) == event
    assert request.request_digest == StartRunRequest.from_dict(request.to_dict()).request_digest
    assert json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True) == json.dumps(
        RunEvent.from_dict(event.to_dict()).to_dict(), ensure_ascii=False, sort_keys=True
    )

    generated_id = StartRunRequest(
        tenant_id="tenant-1",
        user_id="user-1",
        session_id="session-1",
        request_id="request-1",
        request_text="生成报告",
    )
    assigned_id = StartRunRequest(
        tenant_id="tenant-1",
        user_id="user-1",
        session_id="session-1",
        run_id="run-allocated-later",
        request_id="request-1",
        request_text="生成报告",
    )
    other_tenant = StartRunRequest(
        tenant_id="tenant-2",
        user_id="user-1",
        session_id="session-1",
        request_id="request-1",
        request_text="生成报告",
    )
    assert generated_id.request_digest == assigned_id.request_digest
    assert generated_id.request_digest != other_tenant.request_digest


def test_resume_request_round_trips_structured_external_state_evidence() -> None:
    evidence = ExternalStateEvidence(
        resource_id="object:report-17",
        guard_type="OBJECT_VERSION",
        expected_value="v3",
        observed_value="v3",
        checked_at="2026-09-27T10:00:00Z",
        status="VERIFIED",
    )
    request = ResumeRunRequest(
        tenant_id="tenant-1",
        user_id="user-1",
        session_id="session-1",
        run_id="run-1",
        request_id="resume-1",
        external_state_evidence=(evidence,),
    )

    restored = ResumeRunRequest.from_dict(request.to_dict())

    assert restored == request
    assert restored.request_digest == request.request_digest
    assert ExternalStateEvidence.from_dict(evidence.to_dict()) == evidence
    with pytest.raises(ValueError, match="known external guard status"):
        ExternalStateEvidence(
            resource_id="object:report-17",
            guard_type="OBJECT_VERSION",
            status="TRUSTED",
        )


def test_resume_request_digest_includes_bound_no_effect_confirmation() -> None:
    confirmation = NoEffectConfirmation(
        task_id="payment-task",
        idempotency_key="run-1:payment-task",
        external_reference="payment://txn/18",
        checked_at="2026-09-27T10:00:00Z",
    )
    request = ResumeRunRequest(
        tenant_id="tenant-1",
        user_id="user-1",
        session_id="session-1",
        run_id="run-1",
        request_id="resume-1",
        no_effect_confirmation=confirmation,
    )
    changed_confirmation = ResumeRunRequest(
        tenant_id="tenant-1",
        user_id="user-1",
        session_id="session-1",
        run_id="run-1",
        request_id="resume-1",
        no_effect_confirmation=NoEffectConfirmation(
            task_id="payment-task",
            idempotency_key="run-1:payment-task",
            external_reference="payment://txn/19",
            checked_at="2026-09-27T10:00:00Z",
        ),
    )

    restored = ResumeRunRequest.from_dict(request.to_dict())

    assert restored == request
    assert restored.request_digest == request.request_digest
    assert changed_confirmation.request_digest != request.request_digest


def test_public_dto_rejects_live_payload_objects() -> None:
    with pytest.raises(TypeError, match="JSON values only"):
        RunEvent(
            event_id="event-1",
            sequence_number=1,
            tenant_id="tenant-1",
            session_id="session-1",
            run_id="run-1",
            workflow_id=None,
            stage_id=None,
            task_id=None,
            event_type=EventType.RUN_STARTED,
            timestamp="2026-08-06T00:00:00Z",
            payload={"callable": lambda: None},
        )


def test_resume_summary_keeps_disposition_and_action_separate() -> None:
    allowed = ResumeSummary(
        disposition=ResumeDisposition.ALLOW,
        action=ResumeAction.RESUME_EXACT,
        reason_code="VALID",
        requires_clarification=False,
        summary="可以继续",
    )
    assert ResumeSummary.from_dict(allowed.to_dict()) == allowed

    with pytest.raises(ValueError, match="ALLOW requires"):
        ResumeSummary(
            disposition=ResumeDisposition.ALLOW,
            action=None,
            reason_code="INVALID",
            requires_clarification=False,
            summary="",
        )
    with pytest.raises(ValueError, match="non-ALLOW"):
        ResumeSummary(
            disposition=ResumeDisposition.REJECT,
            action=ResumeAction.RESUME_EXACT,
            reason_code="STALE",
            requires_clarification=False,
            summary="拒绝",
        )


def test_snapshot_is_a_small_public_projection() -> None:
    snapshot = RunSnapshot(
        tenant_id="tenant-1",
        run_id="run-1",
        session_id="session-1",
        status=RunStatus.COMPLETED,
        request_text="生成报告",
        active_workflow_id=None,
        request_id="request-1",
        created_at="2026-08-06T00:00:00Z",
        updated_at="2026-08-06T00:00:04Z",
        completed_workflow_ids=("workflow-a",),
        artifacts=(
            ArtifactSummary(
                artifact_id="artifact-1",
                artifact_type="report",
                digest="sha256:1",
                reference="workspace://report.md",
                exists=True,
                verified=True,
                run_id="run-1",
                display_name="report.md",
                size=128,
                producer_workflow_id="workflow-a",
                producer_stage_id="verification",
                created_revision=3,
                created_at="2026-08-06T00:00:03Z",
            ),
        ),
        revision=4,
    )
    payload = snapshot.to_dict()
    assert RunSnapshot.from_dict(payload) == snapshot
    assert "execution_plan" not in payload
    assert "checkpoint_payload" not in payload
    assert "run_resume_index" not in payload
    assert payload["request_id"] == "request-1"
    assert payload["revision"] == 4


def test_event_ordering_and_replay_oracle() -> None:
    events = (
        _event(1, EventType.RUN_STARTED),
        _event(2, EventType.WORKFLOW_STARTED),
        _event(3, EventType.RUN_COMPLETED),
    )
    assert EventOrderingOracle.require_terminal(events, run_id="run-1") == events
    assert tuple(
        event.sequence_number
        for event in EventOrderingOracle.replay_after(events, 1, run_id="run-1")
    ) == (2, 3)

    with pytest.raises(AgentServiceError) as gap:
        EventOrderingOracle.validate(
            (_event(1, EventType.RUN_STARTED), _event(3, EventType.RUN_COMPLETED))
        )
    assert gap.value.code is ServiceErrorCode.EVENT_SEQUENCE_INVALID

    with pytest.raises(AgentServiceError) as identity:
        EventOrderingOracle.validate(events, tenant_id="other-tenant")
    assert identity.value.code is ServiceErrorCode.IDENTITY_MISMATCH

    with pytest.raises(AgentServiceError, match="no event may follow"):
        EventOrderingOracle.validate(
            events + (_event(4, EventType.ARTIFACT_PUBLISHED),)
        )

    blocked_then_resumed = (
        _event(1, EventType.RUN_STARTED),
        _event(2, EventType.RUN_BLOCKED),
        _event(3, EventType.RUN_RESUMED),
        _event(4, EventType.RUN_COMPLETED),
    )
    assert EventOrderingOracle.require_terminal(blocked_then_resumed) == blocked_then_resumed


def test_event_stream_request_rejects_invalid_cursor() -> None:
    with pytest.raises(ValueError, match="after_sequence"):
        EventStreamRequest(
            tenant_id="tenant-1",
            user_id="user-1",
            session_id="session-1",
            run_id="run-1",
            request_id="request-1",
            after_sequence=-1,
        )

    with pytest.raises(AgentServiceError) as expired:
        EventOrderingOracle.replay_after(
            (_event(1, EventType.RUN_STARTED), _event(2, EventType.RUN_COMPLETED)),
            0,
            run_id="run-1",
            oldest_retained_sequence=2,
        )
    assert expired.value.code is ServiceErrorCode.EVENT_CURSOR_EXPIRED


def test_service_error_is_stable_and_does_not_expose_cause() -> None:
    error = AgentServiceError(
        ServiceErrorCode.IDEMPOTENCY_CONFLICT,
        "request already represents another operation",
        retryable=False,
        run_id="run-1",
        request_id="request-1",
        details={"request_id": "request-1"},
    )
    assert error.to_dict() == {
        "code": "IDEMPOTENCY_CONFLICT",
        "message": "request already represents another operation",
        "retryable": False,
        "run_id": "run-1",
        "request_id": "request-1",
        "details": {"request_id": "request-1"},
    }
    assert "Traceback" not in str(error)


def test_v23c_dataset_and_oracle_are_deterministic() -> None:
    cases = build_cases()
    assert validate(cases) == []
    assert benchmark_metadata(cases)["case_count"] == 32
    assert [evaluate(case).to_dict() for case in cases] == [
        evaluate(case).to_dict() for case in cases
    ]
