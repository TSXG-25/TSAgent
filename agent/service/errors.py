"""Stable error taxonomy for the v2.3C AgentService boundary."""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import Any


class ServiceErrorCode(str, Enum):
    INVALID_REQUEST = "INVALID_REQUEST"
    IDENTITY_REQUIRED = "IDENTITY_REQUIRED"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    TENANT_SCOPE_VIOLATION = "TENANT_SCOPE_VIOLATION"
    SESSION_SCOPE_VIOLATION = "SESSION_SCOPE_VIOLATION"
    RUN_ID_REQUIRED = "RUN_ID_REQUIRED"
    REQUEST_ID_REQUIRED = "REQUEST_ID_REQUIRED"
    RUN_NOT_FOUND = "RUN_NOT_FOUND"
    REQUEST_ID_CONFLICT = "REQUEST_ID_CONFLICT"
    DUPLICATE_REQUEST = "DUPLICATE_REQUEST"
    EVENT_SEQUENCE_INVALID = "EVENT_SEQUENCE_INVALID"
    EVENT_REPLAY_UNAVAILABLE = "EVENT_REPLAY_UNAVAILABLE"
    SERVICE_CLOSED = "SERVICE_CLOSED"
    UNSUPPORTED_OPERATION = "UNSUPPORTED_OPERATION"
    INTERNAL_MODEL_LEAK = "INTERNAL_MODEL_LEAK"


class AgentServiceError(RuntimeError):
    """Serializable service failure without a provider exception or traceback."""

    def __init__(
        self,
        code: ServiceErrorCode | str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self.code = ServiceErrorCode(code)
        self.message = str(message)
        self.details = dict(details or {})
        super().__init__(self.message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "message": self.message,
            "details": dict(self.details),
        }

    def __str__(self) -> str:
        return f"{self.code.value}: {self.message}"


__all__ = ["AgentServiceError", "ServiceErrorCode"]
