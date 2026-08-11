from __future__ import annotations

import json

import pytest

from agent.service.local_protocol import (
    LOCAL_TRANSPORT_METHODS,
    LocalProtocolError,
    LocalRpcError,
    LocalRpcFailure,
    LocalRpcRequest,
    LocalRpcSuccess,
    LocalTransportMethod,
    assert_response_id,
    decode_json_line,
    decode_response,
    encode_response,
)


def test_desktop_method_allowlist_is_frozen() -> None:
    assert LOCAL_TRANSPORT_METHODS == (
        "health",
        "start_run",
        "get_run",
        "resume_run",
        "cancel_run",
        "list_artifacts",
        "read_events",
        "shutdown",
    )
    assert tuple(method.value for method in LocalTransportMethod) == LOCAL_TRANSPORT_METHODS


def test_request_jsonl_round_trip_is_canonical_and_json_only() -> None:
    request = LocalRpcRequest(
        id="req-123",
        method=LocalTransportMethod.GET_RUN,
        params={
            "tenant_id": "tenant-1",
            "session_id": "session-1",
            "run_id": "run-1",
            "nested": {"中文": "✅", "items": [1, None, True]},
        },
    )

    line = request.to_json_line()
    assert line.endswith("\n")
    assert "\n" not in line[:-1]
    assert json.loads(line)["method"] == "get_run"
    assert LocalRpcRequest.from_json_line(line) == request
    assert request.to_json_line() == request.to_json_line()


def test_health_and_shutdown_have_no_params() -> None:
    assert LocalRpcRequest("health", LocalTransportMethod.HEALTH).to_dict() == {
        "id": "health",
        "method": "health",
        "params": {},
    }
    with pytest.raises(LocalProtocolError, match="does not accept params"):
        LocalRpcRequest("health", LocalTransportMethod.HEALTH, {"extra": True})


def test_success_and_error_envelopes_round_trip() -> None:
    success = LocalRpcSuccess("req-1", {"status": "ok", "models": ["qwen"]})
    decoded_success = decode_response(encode_response(success))
    assert decoded_success == success

    failure = LocalRpcFailure(
        "req-2",
        LocalRpcError(
            "RUN_NOT_FOUND",
            "run was not found",
            retryable=False,
            run_id="run-2",
            request_id="business-2",
            details={"scope": "tenant-1"},
        ),
    )
    decoded_failure = decode_response(encode_response(failure))
    assert decoded_failure == failure


def test_response_correlation_is_checked() -> None:
    request = LocalRpcRequest("req-1", LocalTransportMethod.HEALTH)
    response = LocalRpcSuccess("req-2", {"status": "ok"})
    with pytest.raises(LocalProtocolError, match="does not match"):
        assert_response_id(request, response)


@pytest.mark.parametrize(
    "line, message",
    [
        (b"not-json\n", "malformed JSON"),
        (b"{\"id\":\"r\",\"method\":\"unknown\",\"params\":{}}\n", "unsupported local method"),
        (b"{\"id\":\"r\",\"method\":\"health\"}\n", "missing required field"),
        (b"{\"id\":\"r\",\"method\":\"health\",\"params\":{\"x\":1}}\n", "does not accept params"),
    ],
)
def test_invalid_request_is_stable(line: bytes, message: str) -> None:
    with pytest.raises(LocalProtocolError, match=message):
        LocalRpcRequest.from_json_line(line)


def test_live_objects_and_non_finite_numbers_are_rejected() -> None:
    with pytest.raises(LocalProtocolError, match="JSON values only"):
        LocalRpcRequest(
            "req-1",
            LocalTransportMethod.START_RUN,
            {"callback": lambda: None},
        ).to_json_line()

    with pytest.raises(LocalProtocolError, match="NaN"):
        LocalRpcSuccess("req-1", {"value": float("nan")}).to_dict()


def test_response_shape_and_public_error_are_not_internal_objects() -> None:
    with pytest.raises(LocalProtocolError, match="success or failure"):
        decode_response('{"id":"req-1","ok":true,"result":{},"traceback":"x"}\n')

    error = LocalRpcError("INTERNAL_ERROR", "operation failed", retryable=False)
    payload = error.to_dict()
    assert "traceback" not in payload
    assert "sqlite" not in json.dumps(payload).lower()
    assert decode_json_line(encode_response(LocalRpcFailure("req-1", error))) == {
        "id": "req-1",
        "ok": False,
        "error": {"code": "INTERNAL_ERROR", "message": "operation failed", "retryable": False},
    }
