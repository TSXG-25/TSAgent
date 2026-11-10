from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from agent.service.contracts import (
    ApprovalSummary,
    DecideApprovalRequest,
    DelegateRunRequest,
    GraphRunStepSummary,
    ListApprovalsRequest,
    RunHandle,
    RunLookupRequest,
    RunSnapshot,
    RunWorkflowGraphRequest,
    StartRunRequest,
)
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

_DESKTOP_TRANSPORT_TS = (
    Path(__file__).resolve().parents[1]
    / "apps"
    / "desktop"
    / "src"
    / "service"
    / "localTransport.ts"
)


def _ts_transport_methods() -> tuple[str, ...]:
    """The TS allowlist is the second copy of the protocol's method table; the
    wire names must line up exactly or a method silently cannot be called."""
    match = re.search(
        r"LOCAL_TRANSPORT_METHODS\s*=\s*\[(.*?)\]\s*as const",
        _DESKTOP_TRANSPORT_TS.read_text(encoding="utf-8"),
        re.DOTALL,
    )
    assert match, f"could not find LOCAL_TRANSPORT_METHODS in {_DESKTOP_TRANSPORT_TS}"
    return tuple(re.findall(r'"([a-z_]+)"', match.group(1)))


def test_desktop_method_allowlist_matches_ts_transport() -> None:
    assert LOCAL_TRANSPORT_METHODS == _ts_transport_methods()
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


def test_wire_contract_fixture_matches_python_dtos() -> None:
    """A16: 契约 fixture 与 Python wire DTO 对齐 — 删改任一已支持
    字段/枚举都会打破本测试（TS 侧同样读取此 fixture）。"""
    import dataclasses
    import json

    fixture = json.loads(
        (Path(__file__).parent / "fixtures" / "wire_contract.json"
         ).read_text())
    method_to_request = {
        "start_run": StartRunRequest,
        "get_run": RunLookupRequest,
        "list_approvals": ListApprovalsRequest,
        "decide_approval": DecideApprovalRequest,
        "delegate_run": DelegateRunRequest,
        "run_graph": RunWorkflowGraphRequest,
    }
    method_to_response = {
        "start_run": RunHandle,
        "get_run": RunSnapshot,
        "list_approvals": ApprovalSummary,
        "decide_approval": ApprovalSummary,
        "delegate_run": None,  # plain dict — covered by delegation tests
        "run_graph": GraphRunStepSummary,
    }
    assert set(fixture["methods"]) == set(method_to_request)
    for method, entry in fixture["methods"].items():
        request_cls = method_to_request[method]
        declared = {
            f.name for f in dataclasses.fields(request_cls)
        }
        assert set(entry["request_fields"]) == declared, method
        dto = request_cls.from_dict(entry["request"])
        assert dto is not None, method
        response_cls = method_to_response[method]
        if response_cls is not None:
            response_fields = {
                f.name for f in dataclasses.fields(response_cls)
            }
            assert set(entry["response_fields"]) == response_fields, method


def test_wire_contract_fixture_matches_ts_client() -> None:
    """A16: TS 侧由 apps/desktop/tests/wireContract.test.ts 校验 —
    这里只校验 fixture 形状，防止漂移两侧都漏。"""
    import json

    fixture = json.loads(
        (Path(__file__).parent / "fixtures" / "wire_contract.json"
         ).read_text())
    for method, entry in fixture["methods"].items():
        for key in ("request", "request_fields",
                    "response_fields", "ts_result_fields"):
            assert key in entry, (method, key)
        for key in entry["request_fields"]:
            assert key in entry["request"] or key in (
                "run_id", "decided_by",
            ), (method, key)  # client 可不发的可选字段


def test_wire_contract_fixture_matches_real_serialization(tmp_path) -> None:
    """A16: fixture 的 response 示例经真实 dispatcher + DTO 序列化
    验证 — 语义漂移（approve→deny、序列化删字段）会让本测试失败。"""
    import asyncio
    import io
    import json

    import tests.test_ha_r1_harness as harness
    from agent.service.local_sidecar.dispatcher import SidecarDispatcher

    fixture = json.loads(
        (Path(__file__).parent / "fixtures" / "wire_contract.json"
         ).read_text())

    async def scenario() -> None:
        service, store = harness.service_at(tmp_path)
        try:
            dispatcher = SidecarDispatcher(
                service, diagnostics=io.StringIO())

            # decide_approval — 真实 dispatch → store → _to_wire → encode。
            store.ensure_session("t", "s")
            store.reserve_service_start(
                "t", "s",
                requested_run_id="run-1", request_id="r-p",
                request_digest="d", writer_id="w",
                external_reference="{}",
            )
            store.create_approval(
                "t", "s", "run-1", "ap-1",
                action="write_file", params_json='{"path":"x"}',
                risk_level="R1",
            )
            response = await dispatcher.dispatch(LocalRpcRequest(
                id="d1",
                method=LocalTransportMethod.DECIDE_APPROVAL,
                params=dict(
                    fixture["methods"]["decide_approval"]["request"]),
            ))
            produced = json.loads(encode_response(response))["result"]
            expected = fixture["methods"]["decide_approval"]["response"]
            for key, value in expected.items():
                assert produced.get(key) == value, (
                    key, produced.get(key))

            # run_graph — 返回的序列化步骤必须带 status 且值正确；
            # 从 to_dict 删一个字段就会破坏这里。
            store.upsert_workflow_graph(
                "t", "g-1",
                definition_json=json.dumps({"nodes": [
                    {"id": "n1", "type": "task", "request_text": "x"},
                ], "edges": []}))
            graph_response = await dispatcher.dispatch(LocalRpcRequest(
                id="g1",
                method=LocalTransportMethod.RUN_GRAPH,
                params=dict(fixture["methods"]["run_graph"]["request"]),
            ))
            steps = json.loads(encode_response(graph_response))["result"]
            assert steps == fixture["methods"]["run_graph"]["response"]
        finally:
            await service.close()

    asyncio.run(scenario())
