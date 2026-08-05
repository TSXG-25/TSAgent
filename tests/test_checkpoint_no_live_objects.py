"""Checkpoint 不允许出现 live object（v2.2A，DeepSeek 真实调用后边界断言）。

真实 SDK 调用后，递归断言 checkpoint payload 只含 JSON 类型；
且把 live object（HTTP response / file handle / callable / coroutine /
generator / exception）塞进 execution_plan 必须被拒绝。
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

_ALLOWED = (str, int, float, bool, type(None))


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


def _assert_json_only(value, path="root"):
    if isinstance(value, dict):
        for k, v in value.items():
            _assert_json_only(v, f"{path}.{k}")
        return
    if isinstance(value, list):
        for i, v in enumerate(value):
            _assert_json_only(v, f"{path}[{i}]")
        return
    if not isinstance(value, _ALLOWED):
        raise AssertionError(f"{path}: 不允许的类型 {type(value).__name__}")


def _mk_checkpoint(execution_plan=None, failure=None) -> RunCheckpoint:
    return RunCheckpoint(
        run_id="run-live-1", checkpoint_id="cp-live-1", parent_checkpoint_id=None,
        sequence_number=0, session_id="sess-1", conversation_id="conv-1",
        user_scope="u1", workflow_id="wf-1", workflow_version="1.0",
        plan_version="1.0", active_stage_id="stage-1", active_task_id="task-1",
        status=CheckpointStatus.RUNNING,
        execution_plan=execution_plan or {"step": "read"},
        target_summary="read", failure_event=failure,
    )


@pytest.mark.skipif(not _ds_ok(), reason="DeepSeek 真实 API 不可达")
class TestNoLiveObjectsAfterRealCall:
    def test_real_call_payload_json_only(self):
        with httpx.Client(timeout=10.0) as c:
            r = c.get(f"{DS_BASE}/models", headers={"Authorization": f"Bearer {_DS_KEY}"})
        assert r.status_code == 200
        # 只把派生的事实（字符串/数字）放入 checkpoint，绝不放入 response 对象
        evidence = RuntimeEvidence(
            source="deepseek", kind="http_response",
            expected="200", observed=str(r.status_code), status="VERIFIED",
            detail=r.headers.get("content-type", ""),
        )
        cp = _mk_checkpoint(execution_plan={"status": r.status_code}, failure=None)
        _assert_json_only(cp.to_dict())  # 递归：只允许 JSON 类型
        assert json.loads(serialize_checkpoint(cp))["execution_plan"]["status"] == 200

    def test_checkpoint_rejects_live_objects(self):
        with httpx.Client(timeout=10.0) as c:
            r = c.get(f"{DS_BASE}/models", headers={"Authorization": f"Bearer {_DS_KEY}"})
        forbidden = (
            r,                      # HTTP response object
            open(__file__, "rb"),   # file handle
            lambda: 1,              # callable
            (i for i in range(3)),  # generator
            BaseException("boom"),  # exception object
        )
        for obj in forbidden:
            with pytest.raises((TypeError, ValueError)):
                _mk_checkpoint(execution_plan={"live": obj})
        try:
            r.close()
        except Exception:
            pass

    def test_failure_event_stores_string_not_exception(self):
        try:
            with httpx.Client(timeout=0.05) as c:
                c.get(f"{DS_BASE}/models", headers={"Authorization": f"Bearer {_DS_KEY}"})
            raise AssertionError("应当超时")
        except httpx.TimeoutException as exc:
            failure = FailureEventSnapshot(
                event_id="ev-live", layer="tool:web",
                symptom="timeout", failure=str(exc),  # 字符串，不是 exception
            )
        cp = _mk_checkpoint(failure=failure)
        _assert_json_only(cp.to_dict())
        assert json.loads(serialize_checkpoint(cp))["failure_event"]["symptom"] == "timeout"
