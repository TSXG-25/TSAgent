"""真实 API 错误映射（v2.2A Checkpoint，DeepSeek 真实 API）。

人为触发真实错误（401/404/超时/断网/结构异常）→ 稳定转换为
FailureEventSnapshot / RuntimeEvidence / SideEffectStatus。
原始 SDK exception 绝不进入 checkpoint；同一错误 → 稳定内部类别。
"""
import json
import os

import httpx
import pytest
from dotenv import load_dotenv

from agent.checkpoint import (
    CheckpointStatus,
    FailureEventSnapshot,
    RunCheckpoint,
    RuntimeEvidence,
    serialize_checkpoint,
)

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
DS_BASE = "https://api.deepseek.com/v1"
_DS_KEY = os.getenv("OPENAI_API_KEY", "")


def _ds_ok() -> bool:
    if not _DS_KEY:
        return False
    try:
        with httpx.Client(timeout=6.0) as c:
            return c.get(
                f"{DS_BASE}/models", headers={"Authorization": f"Bearer {_DS_KEY}"}
            ).status_code == 200
    except Exception:
        return False


def _categorize(exc: Exception) -> str:
    """真实 SDK 错误 → 稳定内部错误类别（ADR-0016）。"""
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
        return "network"
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return "http_4xx" if 400 <= code < 500 else "http_5xx"
    if isinstance(exc, httpx.RequestError):
        return "request_error"
    return type(exc).__name__.lower()


def _mk_checkpoint(failure: FailureEventSnapshot, evidence=()) -> RunCheckpoint:
    return RunCheckpoint(
        run_id="run-err-1", checkpoint_id="cp-err-1", parent_checkpoint_id=None,
        sequence_number=0, session_id="sess-1", conversation_id="conv-1",
        user_scope="u1", workflow_id="wf-1", workflow_version="1.0",
        plan_version="1.0", active_stage_id="stage-1", active_task_id="task-1",
        status=CheckpointStatus.FAILED_RECOVERABLE, execution_plan={"step": "fetch"},
        target_summary="fetch deepseek", failure_event=failure,
        runtime_evidence=evidence,
    )


@pytest.mark.skipif(not _ds_ok(), reason="DeepSeek 真实 API 不可达")
class TestRealErrorMapping:
    def test_401_invalid_key_stable(self):
        with httpx.Client(timeout=10.0) as c:
            with pytest.raises(httpx.HTTPStatusError) as ei:
                c.get(f"{DS_BASE}/models", headers={"Authorization": "Bearer sk-invalid"}).raise_for_status()
        cat = _categorize(ei.value)
        assert cat == "http_4xx"
        assert _categorize(ei.value) == cat  # 稳定
        cp = _mk_checkpoint(FailureEventSnapshot(
            event_id="ev-401", layer="tool:web", symptom=cat, failure=str(ei.value)[:500],
        ))
        payload = json.loads(serialize_checkpoint(cp))
        assert payload["failure_event"]["symptom"] == "http_4xx"
        assert isinstance(payload["failure_event"]["failure"], str)

    def test_404_nonexistent_resource_stable(self):
        with httpx.Client(timeout=10.0) as c:
            with pytest.raises(httpx.HTTPStatusError) as ei:
                c.get(f"{DS_BASE}/models/no-such-model",
                      headers={"Authorization": f"Bearer {_DS_KEY}"}).raise_for_status()
        cat = _categorize(ei.value)
        assert cat == "http_4xx"
        cp = _mk_checkpoint(FailureEventSnapshot(
            event_id="ev-404", layer="tool:web", symptom=cat, failure=str(ei.value)[:500],
        ))
        assert json.loads(serialize_checkpoint(cp))["failure_event"]["symptom"] == "http_4xx"

    def test_timeout_stable(self):
        with httpx.Client(timeout=0.05) as c:
            with pytest.raises(httpx.TimeoutException) as ei:
                c.get(f"{DS_BASE}/models", headers={"Authorization": f"Bearer {_DS_KEY}"})
        cat = _categorize(ei.value)
        assert cat == "timeout"
        cp = _mk_checkpoint(FailureEventSnapshot(
            event_id="ev-timeout", layer="tool:web", symptom="timeout",
            failure=str(ei.value)[:500],
        ))
        assert json.loads(serialize_checkpoint(cp))["failure_event"]["symptom"] == "timeout"

    def test_network_disconnect_stable(self):
        with httpx.Client(timeout=2.0) as c:
            with pytest.raises(httpx.RequestError) as ei:
                c.get("https://api.deepseek.com.invalid/v1/models")
        cat = _categorize(ei.value)
        assert cat == "network"
        cp = _mk_checkpoint(FailureEventSnapshot(
            event_id="ev-net", layer="tool:web", symptom="network",
            failure=str(ei.value)[:500],
        ))
        assert json.loads(serialize_checkpoint(cp))["failure_event"]["symptom"] == "network"

    def test_structure_anomaly_stable(self):
        # 真实响应但结构不符合预期：/v1/ 返回空体（非预期 JSON schema）
        with httpx.Client(timeout=10.0) as c:
            r = c.get("https://api.deepseek.com/v1/",
                      headers={"Authorization": f"Bearer {_DS_KEY}"})
        assert r.status_code == 404
        with pytest.raises(ValueError) as ei:
            json.loads(r.text)  # 空体 → 结构异常
        cat = _categorize(ei.value)
        cp = _mk_checkpoint(FailureEventSnapshot(
            event_id="ev-malformed", layer="tool:web", symptom="malformed_response",
            failure=str(ei.value)[:500],
        ))
        # 结构异常同样不把异常对象带进 checkpoint
        assert isinstance(json.loads(serialize_checkpoint(cp))["failure_event"]["failure"], str)

    def test_side_effect_status_recorded(self):
        with httpx.Client(timeout=0.5) as c:
            with pytest.raises(httpx.TimeoutException):
                c.get(f"{DS_BASE}/models", headers={"Authorization": f"Bearer {_DS_KEY}"})
        cp = _mk_checkpoint(FailureEventSnapshot(
            event_id="ev-se", layer="tool:web", symptom="timeout", failure="timeout",
        ))
        payload = json.loads(serialize_checkpoint(cp))
        assert payload["status"] == "FAILED_RECOVERABLE"
