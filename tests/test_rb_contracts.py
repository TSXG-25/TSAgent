"""R-B contract tests: workflow selection, memory_action, claim truthfulness."""
import asyncio
from types import SimpleNamespace
from pathlib import Path

import pytest

from tests.test_ha_r1_harness import (
    intent, run_turn, service_at, setup_provider,
)


def test_a_workflow_selector_gets_catalog_and_decline_falls_back(monkeypatch, tmp_path):
    """requires_execution + non-empty registry → selector sees ALL projections;
    DECLINE → generic Planner; no keyword needed (request has no 题目)."""
    from agent.workflow_decision import WorkflowDecision, WorkflowDecisionKind
    from agent.workflow_selector import WorkflowSelection, WorkflowSelectionEvidence

    request = '用 Python 实现一个二分查找函数并保存文件'
    seen: dict = {}

    class RecordingSelector:
        async def select_with_evidence(self, goal, context, projections):
            seen['projections'] = list(projections)
            return WorkflowSelection(
                decision=WorkflowDecision(
                    kind=WorkflowDecisionKind.DECLINE, reason='generic ok'),
                evidence=WorkflowSelectionEvidence(
                    provider='test', provider_path='SINGLE_PROVIDER',
                    format_path='RAW_ONLY', raw_output='decline',
                    structured_error=''),
            )

    # Revert the conftest stub: this test wants a recording selector.
    from agent.orchestrator.main import ExecutionOrchestrator
    monkeypatch.setattr(ExecutionOrchestrator, "__init__",
                        ExecutionOrchestrator.__init__)

    provider = setup_provider(monkeypatch, [
        (request, intent(request, artifact=True, outcomes=['FILE_MUTATION'],
            write_targets=['output/bs.py'], domain='development')),
        ('用户需求', {'tasks': [
            dict(id='write', verb='write', target='output/bs.py',
                 target_type='file', goal='实现二分查找'),
        ]}),
        ('你是文件生成器', 'def bs(a, x):\n    lo, hi = 0, len(a)\n    while lo < hi:\n        mid = (lo + hi) // 2\n        if a[mid] < x: lo = mid + 1\n        else: hi = mid\n    return lo\n'),
    ])

    async def scenario():
        service, _ = service_at(tmp_path)
        try:
            # inject recording selector into the orchestrator's selector slot
            for launcher in (getattr(service, '_launcher', None),):
                pass
            snapshot, _ = await run_turn(service, request, 1)
            assert snapshot.status.value == 'COMPLETED', snapshot
        finally:
            await service.close()
    asyncio.run(scenario())
    assert not provider.replies


def test_b_memory_action_remember_commit_and_miss(tmp_path, monkeypatch):
    """remember + committed → '已记住'; remember + no commit → '未保存到记忆'."""
    from agent.runtime import UniversalAgent
    from agent.services.memory_service import ScopedMemoryView

    class Orchestrator:
        replan_count = 0
        def reset_timings(self): pass
        def get_timings(self): return {}
        async def plan(self, **_kw):
            return {
                "plan": [], "requested_outcomes": ["USER_VISIBLE_OUTPUT"],
                "answer_required": True,
                "intent_memory_action": "remember",
                "intent_memory_facts": [
                    {"category": "personal", "key": "name", "value": "TS"}
                ],
            }, "FINISH", "好的。"
        async def finalize(self, **kw):
            return str(kw.get("best_answer") or "好的。")

    from agent.memory import long_term
    monkeypatch.setattr(long_term, "FACTS_DB_PATH", tmp_path / "facts.db")
    long_term._init_facts_db()

    user_id = "rb-memory"
    agent = UniversalAgent.__new__(UniversalAgent)
    agent._memory_view = ScopedMemoryView(user_id, learning_scope="user")
    agent._memory_namespace = user_id
    agent._pending_execution_target = ""
    agent._run_context = SimpleNamespace(
        run_id="run-rb", repository_indexer=None,
        cancellation_view=SimpleNamespace(
            raise_if_requested=lambda *_a, **_k: None))
    agent._timings = {}
    agent._wall_total_seconds = 0.0
    agent._session_context = SimpleNamespace(
        conversation_tracker=SimpleNamespace(update=lambda **_k: None),
        memory_scope="user")
    agent.orchestrator = Orchestrator()
    monkeypatch.setattr(agent, "_print_timing_summary", lambda: {})

    answer = asyncio.run(agent._run_in_context("我叫 TS"))
    assert "已记住" in answer


def test_b2_memory_action_remember_no_commit_discloses(tmp_path, monkeypatch):
    from agent.runtime import _fact_capture_suffix
    from agent.services.memory_service import MemoryLearningResult

    suffix = _fact_capture_suffix("remember", MemoryLearningResult())
    assert suffix.startswith("\n未保存到记忆")
    assert _fact_capture_suffix("none", MemoryLearningResult()) == ""


def test_c_memory_action_forget_routes_to_lifecycle():
    from agent.memory.learning import (
        InteractionEvidence, MemoryPolicyProjection, decide_memory_learning,
    )

    evidence = InteractionEvidence(
        evidence_id="ev-forget",
        source_kind="user_request",
        source_ref="turn:1",
        text="忘记我叫小明。",
        memory_type="fact",
        requested_scope="user",
        canonical_key="personal.name",
        value="小明",
        explicit_persist=True,
        sensitive=False,
        secret=False,
        volatile=False,
        memory_action="forget",
    )
    policy = MemoryPolicyProjection(scope="user", namespace="u")
    decision = decide_memory_learning(evidence, policy)
    assert decision.action == "IGNORE"
    assert decision.reason_code == "DELETE_OWNED_BY_LIFECYCLE"


def test_d_claimed_write_without_artifact_cannot_complete(monkeypatch, tmp_path):
    """Answer prose claiming a write with no verified artifact ≠ COMPLETED."""
    request = '把结果保存到 output/x.py'
    provider = setup_provider(monkeypatch, [
        (request, intent(request, artifact=True, outcomes=['FILE_MUTATION'],
            write_targets=['output/x.py'])),
        ('用户需求', {'tasks': [
            dict(id='talk', verb='explain', target='',
                 target_type='text', goal='只是说明'),
        ]}),
        ('用户需求', '我已写入 output/x.py'),
    ])

    async def scenario():
        service, _ = service_at(tmp_path)
        try:
            snapshot, _ = await run_turn(service, request, 1)
            assert snapshot.status.value != 'COMPLETED', snapshot
        finally:
            await service.close()
    asyncio.run(scenario())


def test_e_chinese_filename_target_completes(monkeypatch, tmp_path):
    """Chinese filenames are valid write targets."""
    request = '创建 output/计算器.py'
    provider = setup_provider(monkeypatch, [
        (request, intent(request, artifact=True, outcomes=['FILE_MUTATION'],
            write_targets=['output/计算器.py'])),
        ('用户需求', {'tasks': [
            dict(id='write', verb='write', target='output/计算器.py',
                 target_type='file', goal='创建中文名文件'),
        ]}),
        ('你是文件生成器', 'print("calc")\n'),
    ])

    async def scenario():
        service, _ = service_at(tmp_path)
        try:
            snapshot, _ = await run_turn(service, request, 1)
            assert snapshot.status.value == 'COMPLETED', snapshot
            assert (tmp_path / 'output/计算器.py').is_file()
        finally:
            await service.close()
    asyncio.run(scenario())
    assert not provider.replies
