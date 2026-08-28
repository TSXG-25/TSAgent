"""Stable error taxonomy for the v2.3C AgentService boundary."""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import Any


class ServiceErrorCode(str, Enum):
    INVALID_REQUEST = "INVALID_REQUEST"
    IDENTITY_REQUIRED = "IDENTITY_REQUIRED"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    TENANT_SCOPE_VIOLATION = "IDENTITY_MISMATCH"
    SESSION_SCOPE_VIOLATION = "IDENTITY_MISMATCH"
    RUN_ID_REQUIRED = "RUN_ID_REQUIRED"
    REQUEST_ID_REQUIRED = "REQUEST_ID_REQUIRED"
    RUN_NOT_FOUND = "RUN_NOT_FOUND"
    RUN_ALREADY_ACTIVE = "RUN_ALREADY_ACTIVE"
    RUN_ALREADY_CANCELLING = "RUN_ALREADY_CANCELLING"
    ALREADY_CANCELLED = "ALREADY_CANCELLED"
    ALREADY_TIMED_OUT = "ALREADY_TIMED_OUT"
    RUN_NOT_CANCELLABLE = "RUN_NOT_CANCELLABLE"
    ALREADY_COMPLETED = "ALREADY_COMPLETED"
    RESUME_NOT_ALLOWED = "RESUME_NOT_ALLOWED"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    REQUEST_ID_CONFLICT = "IDEMPOTENCY_CONFLICT"  # backwards-compatible alias
    DUPLICATE_REQUEST = "DUPLICATE_REQUEST"
    EVENT_SEQUENCE_INVALID = "EVENT_SEQUENCE_INVALID"
    EVENT_CURSOR_EXPIRED = "EVENT_CURSOR_EXPIRED"
    CURSOR_INVALID = "CURSOR_INVALID"
    EVENT_REPLAY_UNAVAILABLE = "CURSOR_INVALID"  # backwards-compatible alias
    STORE_BUSY = "STORE_BUSY"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    SERVICE_CLOSED = "SERVICE_CLOSED"
    UNSUPPORTED_OPERATION = "UNSUPPORTED_OPERATION"
    INTERNAL_MODEL_LEAK = "INTERNAL_MODEL_LEAK"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class AgentServiceError(RuntimeError):
    """Serializable service failure without a provider exception or traceback."""

    def __init__(
        self,
        code: ServiceErrorCode | str,
        message: str,
        *,
        retryable: bool = False,
        run_id: str | None = None,
        request_id: str = "",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self.code = ServiceErrorCode(code)
        self.message = str(message)
        self.retryable = bool(retryable)
        self.run_id = run_id
        self.request_id = str(request_id)
        self.details = dict(details or {})
        super().__init__(self.message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "message": self.message,
            "retryable": self.retryable,
            "run_id": self.run_id,
            "request_id": self.request_id,
            "details": dict(self.details),
        }

    def __str__(self) -> str:
        return f"{self.code.value}: {self.message}"


__all__ = ["AgentServiceError", "ServiceErrorCode"]
