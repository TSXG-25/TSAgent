"""只读资源 ExternalStateGuard（v2.2A Checkpoint，DeepSeek 真实 API + 真实文件）。

读取真实文件 / 远程 /models → 记录 resource_id / content hash / expected /
observed / status → 三种事实（VERIFIED / MISMATCH / MISSING）→
ResumeValidator 确定性决策。
"""
import hashlib
import os
import time

import httpx
import pytest
from dotenv import load_dotenv

from agent.checkpoint import (
    CheckpointStatus,
    ExternalStateGuard,
    ResumeContext,
    RunCheckpoint,
    validate_resume,
)
from agent.checkpoint.reason_codes import (
    GuardStatus,
    ResumeAction,
    ResumeDisposition,
    ResumeReasonCode,
)

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
DS_BASE = "https://api.deepseek.com/v1"
_DS_KEY = os.getenv("OPENAI_API_KEY", "")
_REPO = os.path.join(os.path.dirname(__file__), "..")


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


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _mk_checkpoint(guard: ExternalStateGuard) -> RunCheckpoint:
    return RunCheckpoint(
        run_id="run-guard-1", checkpoint_id="cp-guard-1", parent_checkpoint_id=None,
        sequence_number=0, session_id="sess-1", conversation_id="conv-1",
        user_scope="u1", workflow_id="wf-1", workflow_version="1.0",
        plan_version="1.0", active_stage_id="stage-1", active_task_id="task-1",
        status=CheckpointStatus.SUSPENDED, execution_plan={"step": "read"},
        target_summary="read remote object", external_state_guards=(guard,),
    )


def _mk_context(checkpoint: RunCheckpoint) -> ResumeContext:
    return ResumeContext(
        workflow_id=checkpoint.workflow_id,
        workflow_version=checkpoint.workflow_version,
        plan_version=checkpoint.plan_version,
        requested_action=ResumeAction.RESUME_EXACT,
        requested_target=checkpoint.target_summary,
        candidate_run_ids=(checkpoint.run_id,),
        requested_stage_id=checkpoint.active_stage_id,
    )


class TestFileGuard:
    def _read_repo_file(self):
        path = os.path.join(_REPO, "agent", "checkpoint", "codec.py")
        with open(path, "rb") as f:
            data = f.read()
        return path, data

    def _resume(self, status: GuardStatus) -> tuple:
        path, data = self._read_repo_file()
        digest = _sha256_bytes(data)
        guard = ExternalStateGuard(
            resource_id=os.path.relpath(path, _REPO),
            guard_type="file_content_hash",
            expected_value=digest,
            observed_value=digest if status is GuardStatus.VERIFIED else "deadbeef",
            checked_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            status=status,
        )
        cp = _mk_checkpoint(guard)
        ctx = _mk_context(cp)
        decision = validate_resume(cp, ctx, external_state_evidence=(guard,))
        return decision, digest

    def test_verified_allows(self):
        decision, _ = self._resume(GuardStatus.VERIFIED)
        assert decision.reason_code not in {
            ResumeReasonCode.EXTERNAL_STATE_MISMATCH,
            ResumeReasonCode.EXTERNAL_STATE_UNKNOWN,
        }

    def test_mismatch_rejects(self):
        decision, _ = self._resume(GuardStatus.MISMATCH)
        assert decision.disposition is ResumeDisposition.REJECT
        assert decision.reason_code is ResumeReasonCode.EXTERNAL_STATE_MISMATCH

    def test_missing_clarifies(self):
        decision, _ = self._resume(GuardStatus.MISSING)
        assert decision.disposition is ResumeDisposition.REQUIRE_CLARIFICATION
        assert decision.reason_code is ResumeReasonCode.EXTERNAL_STATE_UNKNOWN


@pytest.mark.skipif(not _ds_ok(), reason="DeepSeek 真实 API 不可达")
class TestRemoteGuard:
    def _fetch_models(self):
        with httpx.Client(timeout=10.0) as c:
            return c.get(f"{DS_BASE}/models", headers={"Authorization": f"Bearer {_DS_KEY}"})

    def test_content_hash_guard_roundtrip(self):
        r1 = self._fetch_models()
        assert r1.status_code == 200
        h1 = _sha256_bytes(r1.content)
        guard = ExternalStateGuard(
            resource_id=f"{DS_BASE}/models", guard_type="content_hash",
            expected_value=h1, observed_value=h1,
            checked_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            status=GuardStatus.VERIFIED,
        )
        cp = _mk_checkpoint(guard)
        decision = validate_resume(cp, _mk_context(cp), external_state_evidence=(guard,))
        assert decision.reason_code not in {
            ResumeReasonCode.EXTERNAL_STATE_MISMATCH,
            ResumeReasonCode.EXTERNAL_STATE_UNKNOWN,
        }
        # 只读资源：再次拉取，hash 应稳定（模型列表不变）
        r2 = self._fetch_models()
        h2 = _sha256_bytes(r2.content)
        assert h1 == h2

    def test_mismatch_rejects(self):
        r = self._fetch_models()
        h = _sha256_bytes(r.content)
        guard = ExternalStateGuard(
            resource_id=f"{DS_BASE}/models", guard_type="content_hash",
            expected_value=h, observed_value="deadbeef", status=GuardStatus.MISMATCH,
        )
        cp = _mk_checkpoint(guard)
        decision = validate_resume(cp, _mk_context(cp), external_state_evidence=(guard,))
        assert decision.disposition is ResumeDisposition.REJECT
        assert decision.reason_code is ResumeReasonCode.EXTERNAL_STATE_MISMATCH

    def test_missing_clarifies(self):
        guard = ExternalStateGuard(
            resource_id=f"{DS_BASE}/no-such-resource", guard_type="content_hash",
            expected_value="", observed_value="", status=GuardStatus.MISSING,
        )
        cp = _mk_checkpoint(guard)
        decision = validate_resume(cp, _mk_context(cp), external_state_evidence=(guard,))
        assert decision.disposition is ResumeDisposition.REQUIRE_CLARIFICATION
        assert decision.reason_code is ResumeReasonCode.EXTERNAL_STATE_UNKNOWN
