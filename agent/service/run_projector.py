"""Projection from one durable Store read snapshot to public DTOs."""

from __future__ import annotations

import json
from dataclasses import fields
from typing import Any, Mapping

from agent.runtime_store.contracts import RunReadSnapshot

from .contracts import (
    ArtifactSummary,
    RunHandle,
    RunLookupRequest,
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
        if active is not None:
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
            "failure": None,
            "failure_summary": None,
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
