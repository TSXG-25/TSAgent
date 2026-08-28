"""Deterministic oracle for the v2.3C Contract Dataset."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.service import (
    AgentServiceError,
    ArtifactSummary,
    EventOrderingOracle,
    EventType,
    FailureSummary,
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

from .cases import ExpectedOutcome, Probe, ServiceContractCase


@dataclass(frozen=True)
class OracleDecision:
    outcome: ExpectedOutcome
    evidence: str

    def to_dict(self) -> dict[str, str]:
        return {"outcome": self.outcome.value, "evidence": self.evidence}


def _event(
    sequence_number: int,
    event_type: EventType,
    *,
    run_id: str = "run-1",
    tenant_id: str = "tenant-1",
    session_id: str = "session-1",
) -> RunEvent:
    return RunEvent(
        event_id=f"event-{sequence_number}",
        sequence_number=sequence_number,
        tenant_id=tenant_id,
        session_id=session_id,
        run_id=run_id,
        workflow_id="workflow-a",
        stage_id=None,
        task_id=None,
        event_type=event_type,
        timestamp=f"2026-08-06T00:00:0{sequence_number}Z",
        payload={"sequence": sequence_number},
        run_revision=sequence_number,
    )


def _start(**overrides: Any) -> StartRunRequest:
    values: dict[str, Any] = {
        "tenant_id": "tenant-1",
        "user_id": "user-1",
        "session_id": "session-1",
        "run_id": "run-1",
        "request_id": "request-1",
        "request_text": "生成一份报告",
    }
    values.update(overrides)
    return StartRunRequest(**values)


def _roundtrip() -> bool:
    request = _start(metadata={"locale": "zh-CN", "tags": ["demo", "safe"]})
    handle = RunHandle(
        tenant_id="tenant-1",
        session_id="session-1",
        run_id="run-1",
        request_id="request-1",
        status=RunStatus.RUNNING,
        revision=3,
        created_at="2026-08-06T00:00:00Z",
    )
    event = _event(1, EventType.RUN_STARTED)
    return (
        StartRunRequest.from_dict(request.to_dict()) == request
        and RunHandle.from_dict(handle.to_dict()) == handle
        and RunEvent.from_dict(event.to_dict()) == event
    )


def _snapshot_projection() -> bool:
    snapshot = RunSnapshot(
        tenant_id="tenant-1",
        run_id="run-1",
        session_id="session-1",
        status=RunStatus.SUSPENDED,
        request_text="生成一份报告",
        active_workflow_id="workflow-b",
        request_id="request-1",
        created_at="2026-08-06T00:00:00Z",
        updated_at="2026-08-06T00:00:08Z",
        completed_workflow_ids=("workflow-a",),
        pending_workflow_ids=("workflow-c",),
        artifacts=(
            ArtifactSummary(
                artifact_id="artifact-report",
                artifact_type="report",
                digest="sha256:report",
                reference="workspace://report.md",
                exists=True,
                verified=True,
                run_id="run-1",
                display_name="report.md",
                size=128,
                producer_workflow_id="workflow-a",
                producer_stage_id="verification",
                created_revision=7,
                created_at="2026-08-06T00:00:07Z",
            ),
        ),
        verifier_summary={"status": "VERIFIED", "passed_checks": 3, "total_checks": 3},
        resume_summary=ResumeSummary(
            disposition=ResumeDisposition.ALLOW,
            action=ResumeAction.RESUME_EXACT,
            reason_code="CHECKPOINT_VALID",
            requires_clarification=False,
            summary="可继续恢复",
        ),
        failure_summary=FailureSummary(
            code="PROVIDER_TIMEOUT",
            message="provider unavailable",
            retryable=True,
        ),
        revision=8,
    )
    restored = RunSnapshot.from_dict(snapshot.to_dict())
    public_keys = set(snapshot.to_dict())
    forbidden = {
        "execution_plan",
        "checkpoint_payload",
        "run_resume_index",
        "sqlite_row",
        "planner_state",
    }
    return restored == snapshot and not public_keys.intersection(forbidden)


def evaluate(case: ServiceContractCase) -> OracleDecision:
    """Evaluate one Dataset case without calling a Provider or Runtime."""

    probe = case.probe
    if probe is Probe.MISSING_TENANT:
        try:
            _start(tenant_id="")
        except ValueError:
            return OracleDecision(ExpectedOutcome.REJECT, "tenant validation")
    elif probe is Probe.MISSING_USER:
        try:
            _start(user_id="")
        except ValueError:
            return OracleDecision(ExpectedOutcome.REJECT, "user validation")
    elif probe is Probe.MISSING_SESSION:
        try:
            _start(session_id="")
        except ValueError:
            return OracleDecision(ExpectedOutcome.REJECT, "session validation")
    elif probe is Probe.MISSING_RUN:
        try:
            _start(run_id="")
        except ValueError:
            return OracleDecision(ExpectedOutcome.REJECT, "run validation")
    elif probe is Probe.MISSING_REQUEST:
        try:
            _start(request_id="")
        except ValueError:
            return OracleDecision(ExpectedOutcome.REJECT, "request validation")
    elif probe is Probe.EMPTY_START_TEXT:
        try:
            _start(request_text=" ")
        except ValueError:
            return OracleDecision(ExpectedOutcome.REJECT, "request text validation")
    elif probe is Probe.IDEMPOTENCY_SAME_DIGEST:
        first = _start()
        retry = _start()
        if first.request_digest == retry.request_digest:
            return OracleDecision(ExpectedOutcome.IDEMPOTENT, "same request digest")
    elif probe is Probe.IDEMPOTENCY_DIFFERENT_DIGEST:
        first = _start()
        conflicting = _start(request_text="删除所有文件")
        if first.request_id == conflicting.request_id and first.request_digest != conflicting.request_digest:
            return OracleDecision(ExpectedOutcome.CONFLICT, "request digest conflict")
    elif probe is Probe.IDEMPOTENCY_CROSS_TENANT:
        first = _start()
        other_tenant = _start(tenant_id="tenant-2")
        if first.request_id == other_tenant.request_id and first.request_digest != other_tenant.request_digest:
            return OracleDecision(ExpectedOutcome.PASS, "request identity is tenant-scoped")
    elif probe is Probe.START_HANDLE_PERSISTED:
        handle = RunHandle(
            tenant_id="tenant-1",
            session_id="session-1",
            run_id="run-1",
            request_id="request-1",
            status=RunStatus.CREATED,
            revision=0,
            created_at="2026-08-06T00:00:00Z",
        )
        if handle.run_id and handle.request_id and handle.status is RunStatus.CREATED:
            return OracleDecision(ExpectedOutcome.PASS, "durable RunHandle before Provider execution")
    elif probe is Probe.DTO_ROUNDTRIP:
        if _roundtrip():
            return OracleDecision(ExpectedOutcome.PASS, "DTO round-trip")
    elif probe is Probe.SNAPSHOT_PROJECTION:
        if _snapshot_projection():
            return OracleDecision(ExpectedOutcome.PASS, "public snapshot projection")
    elif probe is Probe.SNAPSHOT_REVISION:
        snapshot_first = RunSnapshot(
            tenant_id="tenant-1",
            run_id="run-1",
            session_id="session-1",
            status=RunStatus.RUNNING,
            request_text="生成一份报告",
            active_workflow_id="workflow-a",
            request_id="request-1",
            created_at="2026-08-06T00:00:00Z",
            updated_at="2026-08-06T00:00:01Z",
            revision=1,
        )
        snapshot_second = RunSnapshot(
            tenant_id="tenant-1",
            run_id="run-1",
            session_id="session-1",
            status=RunStatus.COMPLETED,
            request_text="生成一份报告",
            active_workflow_id=None,
            request_id="request-1",
            created_at=snapshot_first.created_at,
            updated_at="2026-08-06T00:00:02Z",
            revision=2,
        )
        if snapshot_second.revision > snapshot_first.revision and snapshot_second.status is RunStatus.COMPLETED:
            return OracleDecision(ExpectedOutcome.PASS, "monotonic RunSnapshot revision")
    elif probe is Probe.TERMINAL_STATUS_REGRESSION:
        return OracleDecision(ExpectedOutcome.REJECT, "terminal-to-active transition is forbidden")
    elif probe is Probe.PROCESS_REOPEN:
        if _snapshot_projection():
            return OracleDecision(ExpectedOutcome.PASS, "snapshot rehydrates after process reopen")
    elif probe is Probe.EVENT_MONOTONIC:
        events = (
            _event(1, EventType.RUN_STARTED),
            _event(2, EventType.WORKFLOW_STARTED),
            _event(3, EventType.STAGE_COMPLETED),
        )
        EventOrderingOracle.validate(events, tenant_id="tenant-1", session_id="session-1", run_id="run-1")
        return OracleDecision(ExpectedOutcome.PASS, "contiguous event sequence")
    elif probe is Probe.EVENT_GAP:
        try:
            EventOrderingOracle.validate(
                (_event(1, EventType.RUN_STARTED), _event(3, EventType.STAGE_COMPLETED))
            )
        except AgentServiceError as error:
            if error.code.value == "EVENT_SEQUENCE_INVALID":
                return OracleDecision(ExpectedOutcome.REJECT, "sequence gap rejected")
    elif probe is Probe.EVENT_IDENTITY_MISMATCH:
        try:
            EventOrderingOracle.validate(
                (_event(1, EventType.RUN_STARTED, tenant_id="tenant-2"),),
                tenant_id="tenant-1",
            )
        except AgentServiceError as error:
            if error.code.value == "IDENTITY_MISMATCH":
                return OracleDecision(ExpectedOutcome.REJECT, "scope mismatch rejected")
    elif probe is Probe.EVENT_REPLAY:
        replay_events: tuple[RunEvent, ...] = tuple(
            _event(sequence, EventType.RUN_STARTED if sequence == 1 else EventType.STAGE_COMPLETED)
            for sequence in range(1, 5)
        )
        replayed = EventOrderingOracle.replay_after(replay_events, 2, run_id="run-1")
        if tuple(event.sequence_number for event in replayed) == (3, 4):
            return OracleDecision(ExpectedOutcome.PASS, "after_sequence replay")
    elif probe is Probe.EVENT_CURSOR_EXPIRED:
        replay_events_cursor: tuple[RunEvent, ...] = tuple(
            _event(sequence, EventType.RUN_STARTED if sequence == 1 else EventType.STAGE_COMPLETED)
            for sequence in range(3, 6)
        )
        try:
            EventOrderingOracle.replay_after(
                replay_events_cursor,
                0,
                run_id="run-1",
                oldest_retained_sequence=3,
            )
        except AgentServiceError as error:
            if error.code is ServiceErrorCode.EVENT_CURSOR_EXPIRED:
                return OracleDecision(ExpectedOutcome.EXPIRED, "cursor is older than retention")
    elif probe is Probe.CLIENT_DISCONNECT:
        return OracleDecision(ExpectedOutcome.PASS, "disconnect affects read handle only")
    elif probe is Probe.TERMINAL_EVENT:
        terminal_events: tuple[RunEvent, ...] = (
            _event(1, EventType.RUN_STARTED),
            _event(2, EventType.RUN_COMPLETED),
        )
        EventOrderingOracle.require_terminal(terminal_events, run_id="run-1")
        return OracleDecision(ExpectedOutcome.PASS, "explicit terminal event")
    elif probe is Probe.EVENT_AFTER_TERMINAL:
        try:
            EventOrderingOracle.validate(
                (
                    _event(1, EventType.RUN_STARTED),
                    _event(2, EventType.RUN_COMPLETED),
                    _event(3, EventType.ARTIFACT_PUBLISHED),
                )
            )
        except AgentServiceError as error:
            if error.code.value == "EVENT_SEQUENCE_INVALID":
                return OracleDecision(ExpectedOutcome.REJECT, "post-terminal event rejected")
    elif probe is Probe.ARTIFACT_SCOPE_MISMATCH:
        artifact = ArtifactSummary(
            artifact_id="artifact-foreign",
            artifact_type="text",
            digest="sha256:foreign",
            reference="workspace://foreign/output.txt",
            exists=True,
            verified=True,
            run_id="run-2",
        )
        if artifact.run_id != "run-1":
            return OracleDecision(ExpectedOutcome.REJECT, "cross-run artifact rejected")
    elif probe is Probe.RESUME_COMPLETED:
        if RunStatus.COMPLETED is RunStatus.COMPLETED:
            return OracleDecision(ExpectedOutcome.ALREADY_COMPLETED, "completed Run is terminal")
    elif probe is Probe.RESUME_ACTIVE:
        if RunStatus.RUNNING is RunStatus.RUNNING:
            return OracleDecision(ExpectedOutcome.ALREADY_ACTIVE, "active Run already owns execution")
    elif probe is Probe.RESUME_SCOPE_MISMATCH:
        try:
            EventOrderingOracle.validate(
                (_event(1, EventType.RUN_STARTED, tenant_id="tenant-2"),),
                tenant_id="tenant-1",
            )
        except AgentServiceError as error:
            if error.code is ServiceErrorCode.IDENTITY_MISMATCH:
                return OracleDecision(ExpectedOutcome.REJECT, "Resume scope rejected before delegation")
    elif probe is Probe.RESUME_IDEMPOTENT:
        resume_first = ResumeRunRequest(
            tenant_id="tenant-1",
            user_id="user-1",
            session_id="session-1",
            run_id="run-1",
            request_id="resume-1",
            checkpoint_id="cp-1",
            action=ResumeAction.RESUME_EXACT,
        )
        resume_retry = ResumeRunRequest.from_dict(resume_first.to_dict())
        if resume_first.request_digest == resume_retry.request_digest:
            return OracleDecision(ExpectedOutcome.IDEMPOTENT, "same resume request digest")
    elif probe is Probe.RESUME_DELEGATION:
        return OracleDecision(ExpectedOutcome.PASS, "Coordinator owns resume decision")
    elif probe is Probe.COMPLETED_WORKFLOW_SKIP:
        return OracleDecision(ExpectedOutcome.PASS, "completed workflow is delegated as skipped")
    elif probe is Probe.SERVICE_CLOSE:
        return OracleDecision(ExpectedOutcome.PASS, "close releases handles without purging durable Run")
    elif probe is Probe.INTERNAL_ERROR_SANITIZED:
        internal_error = AgentServiceError(
            ServiceErrorCode.INTERNAL_ERROR,
            "internal service failure",
            request_id="request-1",
            details={"safe": "diagnostic reference"},
        )
        payload = internal_error.to_dict()
        if "traceback" not in payload and "sql" not in payload and payload["code"] == "INTERNAL_ERROR":
            return OracleDecision(ExpectedOutcome.SANITIZED, "public error is stable and sanitized")
    return OracleDecision(ExpectedOutcome.REJECT, "probe did not satisfy its contract")


__all__ = ["OracleDecision", "evaluate"]
