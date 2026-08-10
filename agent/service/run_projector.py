"""Projection from one durable Store read snapshot to public DTOs."""

from __future__ import annotations

import json
from dataclasses import fields
from typing import Any, Mapping

from agent.runtime_store.contracts import RunReadSnapshot

from .contracts import (
    ArtifactSummary,
    FailureSummary,
    RunHandle,
    RunLookupRequest,
    RunOutput,
    RunSnapshot,
    RunStatus,
)
from .errors import AgentServiceError, ServiceErrorCode


_REQUEST_PREFIX = "tsagent-service-request-v1:"


def encode_request_reference(request: Any, *, run_id: str) -> str:
    payload = {
        "tenant_id": request.tenant_id,
        "user_id": request.user_id,
        "session_id": request.session_id,
        "run_id": run_id,
        "request_id": request.request_id,
        "request_text": getattr(request, "request_text", ""),
        "metadata": dict(getattr(request, "metadata", {}) or {}),
    }
    return _REQUEST_PREFIX + json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _request_record(read: RunReadSnapshot) -> dict[str, Any]:
    intent = read.start_intent
    if intent is None or not intent.external_reference.startswith(_REQUEST_PREFIX):
        raise AgentServiceError(
            ServiceErrorCode.RUN_NOT_FOUND,
            "Run has no public Service ownership record",
        )
    try:
        record = json.loads(intent.external_reference[len(_REQUEST_PREFIX) :])
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise AgentServiceError(
            getattr(
                ServiceErrorCode,
                "DURABLE_STORE_FAILURE",
                ServiceErrorCode.INVALID_REQUEST,
            ),
            "durable Service request metadata is invalid",
        ) from error
    if not isinstance(record, dict):
        raise AgentServiceError(
            ServiceErrorCode.RUN_NOT_FOUND,
            "Run Service ownership record is unavailable",
        )
    return record


def _public_status(value: str) -> RunStatus:
    normalized = str(value or "").upper()
    if normalized == "PENDING":
        normalized = "CREATED"
    try:
        return RunStatus(normalized)
    except ValueError:
        return RunStatus.RUNNING


def _dataclass_kwargs(cls: type[Any], values: Mapping[str, Any]) -> dict[str, Any]:
    names = {item.name for item in fields(cls)}
    return {key: value for key, value in values.items() if key in names}


def _artifact_summary(
    artifact: Any,
    *,
    tenant_id: str,
    run_id: str,
    revision: int,
) -> ArtifactSummary:
    values: dict[str, Any] = {
        "artifact_id": artifact.artifact_id,
        "artifact_type": artifact.artifact_type or "artifact",
        "digest": artifact.digest,
        # A public DTO receives an opaque reference, never a filesystem path.
        "reference": f"artifact://{tenant_id}/{run_id}/{artifact.artifact_id}",
        "exists": bool(artifact.exists),
        "verified": bool(artifact.verified),
        "producer_workflow_id": artifact.producer_workflow_id,
        "producer_stage_id": getattr(artifact, "producer_stage_id", None),
        "run_id": run_id,
        "display_name": artifact.artifact_id,
        "size": None,
        "created_revision": revision,
        "created_at": "",
    }
    return ArtifactSummary(**_dataclass_kwargs(ArtifactSummary, values))  # type: ignore[arg-type]


_FAILURE_MESSAGES = {
    "INVALID_REQUEST": "The request was empty or contained only punctuation",
    "RUNTIME_BUDGET_EXHAUSTED": "Run stopped after reaching its execution budget",
    "PROVIDER_TIMEOUT": "Required provider operation timed out",
    "PROVIDER_NETWORK": "Required provider operation was unavailable on the network",
    "PROVIDER_REQUEST_INVALID": "The provider rejected the request",
    "PROVIDER_UNAVAILABLE": "A required provider was unavailable",
    "RESEARCH_TOOL_UNAVAILABLE": "A required research tool was unavailable",
    "UNKNOWN_TOOL": "The execution plan referenced an unavailable tool",
    "UNSUPPORTED_CAPABILITY": "The requested external capability is not available",
    "UNVERIFIED_EFFECT": "The requested effect has no verified execution evidence",
    "UNSUPPORTED_BINARY": "The requested file operation does not support this binary format",
    "PROTECTED_INTERNAL_PATH": "The requested path is protected runtime state",
    "RUNTIME_EXCEPTION": "The Runtime stopped because of an internal execution error",
    "RUNTIME_EXECUTION_INCOMPLETE": "The Run did not produce all required outputs",
    "TASK_EXECUTION_FAILED": "At least one required task failed",
    "MISSING_USER_OUTPUT": "The Run completed its internal work but produced no user-visible output",
    "MISSING_PREVIOUS_OUTPUT": "No previous user-visible Run output is available",
}


def _failure_summary(read: RunReadSnapshot) -> FailureSummary | None:
    event = read.terminal_event
    if event is None or event.event_type not in {"run_failed", "run_blocked"}:
        return None
    try:
        payload = json.loads(event.payload_json)
    except (TypeError, ValueError, json.JSONDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    code = str(payload.get("failure_code", "") or "")
    if not code:
        code = "RUN_BLOCKED" if event.event_type == "run_blocked" else "RUNTIME_EXECUTION_FAILED"
    details: dict[str, Any] = {}
    for key in ("failure_class", "failed_component", "diagnostic_event_id"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            details[key] = value.strip()[:120]
    return FailureSummary(
        code=code,
        message=_FAILURE_MESSAGES.get(code, "The Run did not complete successfully"),
        retryable=bool(payload.get("retryable", False)),
        details=details,
    )


def _run_output(read: RunReadSnapshot) -> RunOutput | None:
    output = getattr(read, "output", None)
    if output is None:
        return None
    return RunOutput(
        run_id=output.run_id,
        revision=output.revision,
        text=output.text,
        evidence_ids=output.evidence_ids,
        artifact_ids=output.artifact_ids,
        created_at=output.created_at,
    )


class RunProjector:
    """Project one ``RunReadSnapshot`` without performing additional reads."""

    def project(self, read: RunReadSnapshot, request: RunLookupRequest) -> RunSnapshot:
        record = _request_record(read)
        expected = {
            "tenant_id": request.tenant_id,
            "user_id": request.user_id,
            "session_id": request.session_id,
            "run_id": request.run_id,
        }
        for key, value in expected.items():
            recorded = str(record.get(key, ""))
            # A generated Run ID is assigned by the atomic Store reservation;
            # the durable head is authoritative for that one field.
            if key == "run_id" and not recorded:
                recorded = read.head.run_id
            if recorded != value:
                # Public lookup must not reveal whether another scope owns the
                # requested run.
                raise AgentServiceError(
                    ServiceErrorCode.RUN_NOT_FOUND,
                    "Run was not found in the requested scope",
                )

        index = read.index
        completed = tuple(getattr(index, "completed_workflow_ids", ())) if index else ()
        pending = tuple(getattr(index, "pending_workflow_ids", ())) if index else ()
        active_id = getattr(index, "active_workflow_id", "") if index else ""
        active = index.workflow(active_id) if index and active_id else None
        head_status = _public_status(read.head.run_status)
        terminal_statuses = {
            RunStatus.COMPLETED,
            RunStatus.FAILED_TERMINAL,
            RunStatus.BLOCKED,
            RunStatus.CANCELLED,
            RunStatus.TIMED_OUT,
        }
        # The durable Run head and terminal event are authoritative.  An old
        # or partially published RunResumeIndex must not turn a failed Run
        # into a public COMPLETED snapshot.
        if (
            read.terminal_event is not None
            or head_status in terminal_statuses
            or head_status is RunStatus.CANCELLING
        ):
            status = head_status
            verifier = "VERIFIED" if status is RunStatus.COMPLETED else None
        elif active is not None:
            status = _public_status(active.status.value)
            verifier = str(active.verifier_status or "UNKNOWN")
        elif index is not None and not pending and completed:
            status = RunStatus.COMPLETED
            verifier = "VERIFIED"
        else:
            status = _public_status(read.head.run_status)
            verifier = None

        artifacts = tuple(
            _artifact_summary(
                artifact,
                tenant_id=request.tenant_id,
                run_id=request.run_id,
                revision=read.head.current_revision,
            )
            for artifact in (getattr(index, "artifacts", ()) if index else ())
        )
        values: dict[str, Any] = {
            "tenant_id": request.tenant_id,
            "run_id": request.run_id,
            "session_id": request.session_id,
            "status": status,
            "request_text": str(record.get("request_text", "")),
            "active_workflow_id": active_id or None,
            "request_id": str(record.get("request_id", read.start_intent.request_id if read.start_intent else "")),
            "created_at": str(read.start_intent.created_at if read.start_intent else read.head.updated_at),
            "updated_at": read.head.updated_at,
            "completed_workflow_ids": completed,
            "pending_workflow_ids": pending,
            "artifacts": artifacts,
            "verifier_status": verifier,
            "verifier_summary": ({"status": verifier} if verifier else None),
            "failure": _failure_summary(read),
            "failure_summary": _failure_summary(read),
            "output": _run_output(read),
            "resume_summary": None,
            "revision": read.head.current_revision,
        }
        try:
            return RunSnapshot(**_dataclass_kwargs(RunSnapshot, values))  # type: ignore[arg-type]
        except (TypeError, ValueError) as error:
            raise AgentServiceError(
                ServiceErrorCode.INTERNAL_MODEL_LEAK
                if "model" in str(error).lower()
                else ServiceErrorCode.INVALID_REQUEST,
                "Run snapshot projection failed",
            ) from error

    def artifacts(
        self,
        read: RunReadSnapshot,
        request: RunLookupRequest,
    ) -> tuple[ArtifactSummary, ...]:
        snapshot = self.project(read, request)
        return snapshot.artifacts


def handle_from_head(head: Any, *, request_id: str) -> RunHandle:
    values = {
        "tenant_id": head.tenant_id,
        "session_id": head.session_id,
        "run_id": head.run_id,
        "request_id": request_id,
        "status": _public_status(head.run_status),
        "revision": head.current_revision,
        "created_at": head.updated_at,
        "updated_at": head.updated_at,
    }
    return RunHandle(**_dataclass_kwargs(RunHandle, values))  # type: ignore[arg-type]


__all__ = [
    "RunProjector",
    "encode_request_reference",
    "handle_from_head",
]
