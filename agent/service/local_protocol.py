"""JSON-lines protocol types for the Desktop MVP-2 local transport.

This module defines transport facts only.  It deliberately does not import the
Runtime, SQLite store, Orchestrator, or cancellation implementation.  The
future sidecar will decode these envelopes, construct the existing public
AgentService DTOs, and encode the resulting DTOs again.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, NoReturn, cast


PROTOCOL_VERSION = "desktop-local-jsonl-v1"
MAX_JSON_LINE_BYTES = 1_048_576


class LocalTransportMethod(str, Enum):
    HEALTH = "health"
    START_RUN = "start_run"
    GET_RUN = "get_run"
    RESUME_RUN = "resume_run"
    CANCEL_RUN = "cancel_run"
    LIST_ARTIFACTS = "list_artifacts"
    READ_EVENTS = "read_events"
    SHUTDOWN = "shutdown"


LOCAL_TRANSPORT_METHODS: tuple[str, ...] = tuple(
    method.value for method in LocalTransportMethod
)


class LocalProtocolError(ValueError):
    """Stable protocol error safe to project into an RPC failure envelope."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = dict(details or {})
        super().__init__(message)

    def to_error_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.details:
            payload["details"] = _thaw_json(_freeze_json(self.details))
        return payload


def _fail(
    code: str,
    message: str,
    *,
    details: Mapping[str, Any] | None = None,
) -> NoReturn:
    raise LocalProtocolError(code, message, details=details)


def _required_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail("INVALID_REQUEST", f"{label} must be a non-empty string")
    return value.strip()


def _freeze_json(value: Any, path: str = "$") -> Any:
    """Copy and validate JSON-shaped data, rejecting live process objects."""

    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            _fail("INVALID_REQUEST", f"{path} must not contain NaN or infinity")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                _fail("INVALID_REQUEST", f"{path} object keys must be strings")
            frozen[key] = _freeze_json(item, f"{path}.{key}")
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item, f"{path}[{index}]") for index, item in enumerate(value))
    _fail(
        "INVALID_REQUEST",
        f"{path} must contain JSON values only; got {type(value).__name__}",
    )


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    if isinstance(value, list):
        return [_thaw_json(item) for item in value]
    return value


def _require_object(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail("INVALID_REQUEST", f"{label} must be a JSON object")
    for key in value:
        if not isinstance(key, str):
            _fail("INVALID_REQUEST", f"{label} keys must be strings")
    return cast(Mapping[str, Any], value)


def _require_exact_keys(
    value: Mapping[str, Any],
    required: set[str],
    label: str,
) -> None:
    missing = sorted(required - set(value))
    if missing:
        _fail("INVALID_REQUEST", f"{label} missing required field(s): {', '.join(missing)}")
    unexpected = sorted(set(value) - required)
    if unexpected:
        _fail(
            "INVALID_REQUEST",
            f"{label} contains unsupported field(s): {', '.join(unexpected)}",
        )


def _canonical_json(value: Mapping[str, Any]) -> str:
    try:
        encoded = json.dumps(
            _thaw_json(_freeze_json(value)),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:  # pragma: no cover - guarded by _freeze_json
        raise LocalProtocolError("INVALID_REQUEST", "payload is not JSON serializable") from exc
    return encoded


def encode_json_line(value: Mapping[str, Any]) -> str:
    """Encode one protocol message, including its terminating newline."""

    line = _canonical_json(_require_object(value, "message")) + "\n"
    if len(line.encode("utf-8")) > MAX_JSON_LINE_BYTES:
        raise LocalProtocolError(
            "INVALID_REQUEST",
            f"JSON line exceeds {MAX_JSON_LINE_BYTES} bytes",
        )
    return line


def decode_json_line(line: str | bytes) -> dict[str, Any]:
    """Decode exactly one UTF-8 JSON-lines message into a plain dictionary."""

    if isinstance(line, bytes):
        if len(line) > MAX_JSON_LINE_BYTES:
            _fail("INVALID_REQUEST", f"JSON line exceeds {MAX_JSON_LINE_BYTES} bytes")
        try:
            text = line.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise LocalProtocolError("INVALID_REQUEST", "JSON line is not valid UTF-8") from exc
    elif isinstance(line, str):
        if len(line.encode("utf-8")) > MAX_JSON_LINE_BYTES:
            _fail("INVALID_REQUEST", f"JSON line exceeds {MAX_JSON_LINE_BYTES} bytes")
        text = line
    else:
        _fail("INVALID_REQUEST", "JSON line must be text or UTF-8 bytes")

    text = text.rstrip("\r\n")
    if not text or "\n" in text or "\r" in text:
        _fail("INVALID_REQUEST", "transport input must contain exactly one JSON line")

    def reject_constant(token: str) -> None:
        raise ValueError(token)

    try:
        decoded = json.loads(text, parse_constant=reject_constant)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise LocalProtocolError("INVALID_REQUEST", "malformed JSON line") from exc
    message = _require_object(decoded, "message")
    return cast(dict[str, Any], dict(message))


@dataclass(frozen=True)
class LocalRpcRequest:
    id: str
    method: LocalTransportMethod
    params: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _required_string(self.id, "id"))
        try:
            object.__setattr__(self, "method", LocalTransportMethod(self.method))
        except ValueError as exc:
            raise LocalProtocolError(
                "UNSUPPORTED_OPERATION",
                f"unsupported local method: {self.method}",
            ) from exc
        object.__setattr__(self, "params", _require_frozen_object(self.params, "params"))
        if self.method in {LocalTransportMethod.HEALTH, LocalTransportMethod.SHUTDOWN}:
            if self.params:
                _fail("INVALID_REQUEST", f"{self.method.value} does not accept params")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "method": self.method.value,
            "params": _thaw_json(self.params),
        }

    def to_json_line(self) -> str:
        return encode_json_line(self.to_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "LocalRpcRequest":
        message = _require_object(value, "request")
        _require_exact_keys(message, {"id", "method", "params"}, "request")
        return cls(
            id=_required_string(message["id"], "id"),
            method=cast(LocalTransportMethod, message["method"]),
            params=_require_object(message["params"], "params"),
        )

    @classmethod
    def from_json_line(cls, line: str | bytes) -> "LocalRpcRequest":
        return cls.from_dict(decode_json_line(line))


@dataclass(frozen=True)
class LocalRpcError:
    code: str
    message: str
    retryable: bool
    run_id: str | None = None
    request_id: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", _required_string(self.code, "error.code"))
        if not isinstance(self.message, str):
            _fail("INVALID_RESPONSE", "error.message must be a string")
        if not isinstance(self.retryable, bool):
            _fail("INVALID_RESPONSE", "error.retryable must be a boolean")
        if self.run_id is not None:
            object.__setattr__(self, "run_id", _required_string(self.run_id, "error.run_id"))
        if self.request_id is not None:
            object.__setattr__(
                self,
                "request_id",
                _required_string(self.request_id, "error.request_id"),
            )
        object.__setattr__(self, "details", _require_frozen_object(self.details, "error.details"))

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.run_id is not None:
            value["run_id"] = self.run_id
        if self.request_id is not None:
            value["request_id"] = self.request_id
        if self.details:
            value["details"] = _thaw_json(self.details)
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "LocalRpcError":
        message = _require_object(value, "error")
        allowed = {"code", "message", "retryable", "run_id", "request_id", "details"}
        unexpected = sorted(set(message) - allowed)
        if unexpected:
            _fail("INVALID_RESPONSE", f"error contains unsupported field(s): {', '.join(unexpected)}")
        if not {"code", "message", "retryable"}.issubset(message):
            _fail("INVALID_RESPONSE", "error requires code, message, and retryable")
        return cls(
            code=message["code"],
            message=message["message"],
            retryable=message["retryable"],
            run_id=message.get("run_id"),
            request_id=message.get("request_id"),
            details=message.get("details", {}),
        )


@dataclass(frozen=True)
class LocalRpcSuccess:
    id: str
    result: Any

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _required_string(self.id, "id"))
        object.__setattr__(self, "result", _freeze_json(self.result))

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "ok": True, "result": _thaw_json(self.result)}


@dataclass(frozen=True)
class LocalRpcFailure:
    id: str
    error: LocalRpcError

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _required_string(self.id, "id"))
        if not isinstance(self.error, LocalRpcError):
            raise TypeError("error must be a LocalRpcError")

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "ok": False, "error": self.error.to_dict()}


LocalRpcResponse = LocalRpcSuccess | LocalRpcFailure


def parse_response(value: Mapping[str, Any]) -> LocalRpcResponse:
    message = _require_object(value, "response")
    if set(message) == {"id", "ok", "result"} and message.get("ok") is True:
        return LocalRpcSuccess(id=message["id"], result=message["result"])
    if set(message) == {"id", "ok", "error"} and message.get("ok") is False:
        return LocalRpcFailure(id=message["id"], error=LocalRpcError.from_dict(message["error"]))
    _fail("INVALID_RESPONSE", "response must be a success or failure envelope")


def encode_response(response: LocalRpcResponse) -> str:
    return encode_json_line(response.to_dict())


def decode_response(line: str | bytes) -> LocalRpcResponse:
    return parse_response(decode_json_line(line))


def assert_response_id(request: LocalRpcRequest, response: LocalRpcResponse) -> None:
    if request.id != response.id:
        _fail(
            "INVALID_RESPONSE",
            "response id does not match request id",
            details={"request_id": request.id, "response_id": response.id},
        )


def _require_frozen_object(value: Any, label: str) -> Mapping[str, Any]:
    object_value = _require_object(value, label)
    frozen = _freeze_json(object_value, label)
    if not isinstance(frozen, Mapping):  # pragma: no cover - guarded above
        _fail("INVALID_REQUEST", f"{label} must be a JSON object")
    return cast(Mapping[str, Any], frozen)


RpcRequest = LocalRpcRequest
RpcError = LocalRpcError
RpcSuccess = LocalRpcSuccess
RpcFailure = LocalRpcFailure


__all__ = [
    "LOCAL_TRANSPORT_METHODS",
    "MAX_JSON_LINE_BYTES",
    "PROTOCOL_VERSION",
    "LocalProtocolError",
    "LocalRpcError",
    "LocalRpcFailure",
    "LocalRpcRequest",
    "LocalRpcResponse",
    "LocalRpcSuccess",
    "LocalTransportMethod",
    "RpcError",
    "RpcFailure",
    "RpcRequest",
    "RpcSuccess",
    "assert_response_id",
    "decode_json_line",
    "decode_response",
    "encode_json_line",
    "encode_response",
    "parse_response",
]
