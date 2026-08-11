"""Safe DTO and error projection for the Desktop local sidecar."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..errors import AgentServiceError
from ..local_protocol import (
    LocalProtocolError,
    LocalRpcError,
    LocalRpcFailure,
    LocalRpcRequest,
    LocalRpcResponse,
    LocalRpcSuccess,
)


_SAFE_ERROR_MESSAGES = {
    "INVALID_REQUEST": "request is invalid",
    "IDENTITY_MISMATCH": "request identity is not valid for this scope",
    "RUN_NOT_FOUND": "run was not found",
    "RUN_ALREADY_ACTIVE": "run is already active",
    "RUN_ALREADY_CANCELLING": "run is already cancelling",
    "ALREADY_CANCELLED": "run is already cancelled",
    "ALREADY_TIMED_OUT": "run has already timed out",
    "RUN_NOT_CANCELLABLE": "run cannot be cancelled",
    "ALREADY_COMPLETED": "run is already completed",
    "RESUME_NOT_ALLOWED": "run cannot be resumed",
    "IDEMPOTENCY_CONFLICT": "request conflicts with an existing operation",
    "EVENT_SEQUENCE_INVALID": "event sequence is invalid",
    "EVENT_CURSOR_EXPIRED": "event cursor is no longer readable",
    "CURSOR_INVALID": "event cursor is invalid",
    "STORE_BUSY": "durable store is busy",
    "PROVIDER_UNAVAILABLE": "provider is unavailable",
    "SERVICE_CLOSED": "service is closed",
    "UNSUPPORTED_OPERATION": "operation is not supported",
    "INTERNAL_ERROR": "internal service error",
}


def _safe_service_message(code: str) -> str:
    return _SAFE_ERROR_MESSAGES.get(code, "service request failed")


def dto_to_wire(value: Any) -> Any:
    """Project public DTOs and containers without exposing internal objects."""

    if hasattr(value, "to_dict") and callable(value.to_dict):
        return dto_to_wire(value.to_dict())
    if isinstance(value, Mapping):
        return {str(key): dto_to_wire(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [dto_to_wire(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise LocalProtocolError(
        "INTERNAL_MODEL_LEAK",
        f"service result contains unsupported object: {type(value).__name__}",
    )


def success_response(request: LocalRpcRequest, result: Any) -> LocalRpcSuccess:
    return LocalRpcSuccess(request.id, dto_to_wire(result))


def service_error_response(
    request: LocalRpcRequest,
    error: AgentServiceError,
) -> LocalRpcFailure:
    code = error.code.value
    return LocalRpcFailure(
        request.id,
        LocalRpcError(
            code=code,
            message=_safe_service_message(code),
            retryable=error.retryable,
            run_id=error.run_id,
            request_id=error.request_id or None,
        ),
    )


def protocol_error_response(
    request_id: str,
    error: LocalProtocolError,
) -> LocalRpcFailure:
    return LocalRpcFailure(
        request_id,
        LocalRpcError(
            code=error.code,
            message=error.message,
            retryable=error.retryable,
            details=error.details,
        ),
    )


def internal_error_response(
    request_id: str,
    *,
    retryable: bool = False,
) -> LocalRpcFailure:
    return LocalRpcFailure(
        request_id,
        LocalRpcError(
            code="INTERNAL_ERROR",
            message="internal service error",
            retryable=retryable,
        ),
    )


__all__ = [
    "dto_to_wire",
    "internal_error_response",
    "protocol_error_response",
    "service_error_response",
    "success_response",
]
