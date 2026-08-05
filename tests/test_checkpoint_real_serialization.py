"""真实只读 API 响应序列化（v2.2A Checkpoint，DeepSeek 真实 API）。

真实 GET /models → RuntimeEvidence / ArtifactSnapshot → RunCheckpoint →
canonical JSON → decode → digest 一致。
重点：时间戳 / null 与缺字段 / Unicode / 浮点 / 嵌套 / 字段顺序 /
超长响应只存引用 / 敏感字段不入 checkpoint。
"""
import hashlib
import json
import os

import httpx
import pytest
from dotenv import load_dotenv

from agent.checkpoint import (
    ArtifactSnapshot,
    CheckpointStatus,
    RunCheckpoint,
    RuntimeEvidence,
    checkpoint_digest,
    deserialize_checkpoint,
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
            r = c.get(f"{DS_BASE}/models", headers={"Authorization": f"Bearer {_DS_KEY}"})
        return r.status_code == 200
    except Exception:
        return False


def _mk_checkpoint(execution_plan: dict, evidence=(), artifacts=()) -> RunCheckpoint:
    return RunCheckpoint(
        run_id="run-ds-1", checkpoint_id="cp-ds-1", parent_checkpoint_id=None,
        sequence_number=0, session_id="sess-1", conversation_id="conv-1",
        user_scope="u1", workflow_id="wf-1", workflow_version="1.0",
        plan_version="1.0", active_stage_id="stage-1", active_task_id="task-1",
        status=CheckpointStatus.RUNNING, execution_plan=execution_plan,
        target_summary="read deepseek models", artifacts=artifacts,
        runtime_evidence=evidence,
    )


@pytest.mark.skipif(not _ds_ok(), reason="DeepSeek 真实 API 不可达")
class TestRealResponseRoundtrip:
    def _models(self):
        with httpx.Client(timeout=30.0) as c:
            r = c.get(f"{DS_BASE}/models", headers={"Authorization": f"Bearer {_DS_KEY}"})
        return r

    def test_roundtrip_digest_stable(self):
        r = self._models()
        assert r.status_code == 200
        payload = r.json()
        digest = hashlib.sha256(r.content).hexdigest()

        evidence = RuntimeEvidence(
            source="deepseek", kind="http_response",
            expected="200", observed=str(r.status_code), status="VERIFIED",
            detail=r.headers.get("content-type", ""),
        )
        artifact = ArtifactSnapshot(
            artifact_id=f"{DS_BASE}/models", artifact_type="http_json",
            digest=digest, exists=True, reference=f"{DS_BASE}/models",
        )
        cp = _mk_checkpoint({"api": payload}, evidence=(evidence,), artifacts=(artifact,))

        payload_s = serialize_checkpoint(cp)
        cp2 = deserialize_checkpoint(payload_s)
        assert checkpoint_digest(cp) == checkpoint_digest(cp2)
        assert cp2.artifacts[0].digest == digest
        # 用 to_dict()（解冻后的 JSON 结构）与真实响应比较
        thawed_api = cp2.to_dict()["execution_plan"]["api"]
        assert thawed_api["object"] == "list"
        assert isinstance(thawed_api["data"], list)
        assert thawed_api == payload

    def test_field_order_does_not_change_digest(self):
        payload = self._models().json()
        cp_a = _mk_checkpoint({"a": 1, "b": payload, "c": {"d": [1, 2]}})
        cp_b = _mk_checkpoint({"c": {"d": [1, 2]}, "b": payload, "a": 1})
        assert checkpoint_digest(cp_a) == checkpoint_digest(cp_b)

    def test_timestamps_null_unicode_floats_preserved(self):
        api = self._models().json()
        plan = {
            "api": api,                 # 真实响应（嵌套 dict + list）
            "created_at": "2026-08-05T09:30:00.000Z",
            "null_field": None,
            "missing_is_default": "",   # 模拟缺字段
            "float_sample": 3.141592653589793,
            "unicode": "中文测试——emoji 😀 + ünïcode",
        }
        cp = _mk_checkpoint(plan)
        cp2 = deserialize_checkpoint(serialize_checkpoint(cp))
        assert cp2.execution_plan["created_at"] == plan["created_at"]
        assert cp2.execution_plan["null_field"] is None
        assert cp2.execution_plan["float_sample"] == 3.141592653589793
        assert cp2.execution_plan["unicode"] == plan["unicode"]
        assert cp2.to_dict()["execution_plan"]["api"] == api
        assert checkpoint_digest(cp) == checkpoint_digest(cp2)

    def test_content_stored_as_reference_only(self):
        r = self._models()
        digest = hashlib.sha256(r.content).hexdigest()
        artifact = ArtifactSnapshot(
            artifact_id="deepseek-models", artifact_type="http_json",
            digest=digest, exists=True, reference=f"{DS_BASE}/models",
        )
        cp = _mk_checkpoint({"size": len(r.content)}, artifacts=(artifact,))
        payload = serialize_checkpoint(cp)
        # 只存 digest+reference；完整响应体不入 checkpoint
        assert len(payload) < 5000
        assert artifact.digest in payload
        # 响应体特征（如 owned_by 字段）不应整份出现在 checkpoint
        assert '"owned_by"' not in payload

    def test_sensitive_fields_not_in_checkpoint(self):
        r = self._models()
        secret = _DS_KEY
        evidence = RuntimeEvidence(
            source="deepseek", kind="http_response",
            expected="200", observed=str(r.status_code), status="VERIFIED",
        )
        cp = _mk_checkpoint({"object": r.json().get("object", "")}, evidence=(evidence,))
        payload = serialize_checkpoint(cp)
        assert secret not in payload
        assert "Authorization" not in payload
        assert "Bearer" not in payload
