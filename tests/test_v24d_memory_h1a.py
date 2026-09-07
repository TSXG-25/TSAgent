"""H1a regressions for natural memory entry, recall, and truthful acknowledgement."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.cognition.cognitive_context import CognitiveContext
from agent.cognition.intent_engine import IntentEngine
from agent.cognition.intent_schema import DOMAIN_MEMORY
from agent.context_policy import ContextMode, ContextPolicy
from agent.memory.learning import decide_memory_learning
from agent.memory.learning_provider import (
    MemoryLearningSelection,
    MemoryLearningSelectionEvidence,
    MemoryLearningProvider,
)
from agent.memory.persistence import MemoryCommitEvidence
from agent.runtime import _normalize_fact_capture_answer
from agent.services.memory_service import (
    MemoryLearningResult,
    MemoryService,
    _user_memory_candidates,
)


@pytest.mark.parametrize(
    "text",
    [
        "以后 Python 项目优先用 uv",
        "我平常 Python 都用 uv",
        "Python 项目默认帮我用 uv",
        "我一般用 uv 管 Python 环境",
        "以后别用 pip，优先 uv",
        "写代码时少加没必要的 defensive checks",
        "技术回答我喜欢先给结论再解释",
    ],
)
def test_natural_preference_opens_post_turn_learning(text: str) -> None:
    policy = ContextPolicy.for_request(text)

    assert policy.mode is ContextMode.MEMORY, text
    assert policy.pre_answer_fact_extraction is False, text
    assert policy.post_answer_fact_extraction is True, text


@pytest.mark.parametrize(
    "text",
    [
        "我平时 Python 项目偏好用什么？",
        "按我平常 Python 的方式来",
        "我之前说写代码有什么偏好？",
        "按照我的开发习惯初始化这个项目",
        "根据我的回答习惯来组织说明",
    ],
)
def test_implicit_memory_request_opens_scoped_retrieval(text: str) -> None:
    policy = ContextPolicy.for_request(text)

    assert policy.mode is ContextMode.MEMORY, text
    assert policy.memory_retrieval is True, text
    assert policy.repository_retrieval is False, text
    assert policy.post_answer_fact_extraction is False, text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "以后 Python 项目优先用 uv",
            [("programming", "python_environment", "preference", "uv")],
        ),
        (
            "我平常 Python 都用 uv",
            [("programming", "python_environment", "preference", "uv")],
        ),
        (
            "Python 项目默认帮我用 uv",
            [("programming", "python_environment", "preference", "uv")],
        ),
        (
            "我一般用 uv 管 Python 环境",
            [("programming", "python_environment", "preference", "uv")],
        ),
        (
            "以后别用 pip，优先 uv",
            [("programming", "python_environment", "preference", "uv")],
        ),
        (
            "写代码别加太多没必要的 defensive checks",
            [("programming", "code_style", "preference", "别加太多没必要的 defensive checks")],
        ),
        (
            "技术回答我喜欢先给结论再解释",
            [("response", "answer_style", "preference", "先给结论再解释")],
        ),
    ],
)
def test_natural_preference_projection_has_stable_keys(
    text: str,
    expected: list[tuple[str, str, str, str]],
) -> None:
    assert _user_memory_candidates(text) == expected


def test_natural_preference_uses_the_existing_commit_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from agent.memory import long_term

    monkeypatch.setattr(long_term, "FACTS_DB_PATH", tmp_path / "facts.db")
    long_term._init_facts_db()

    async def deterministic_provider(self, evidence, existing, policy):
        decision = decide_memory_learning(evidence, policy)
        return MemoryLearningSelection(
            decision=decision,
            evidence=MemoryLearningSelectionEvidence(
                provider="test-provider",
                provider_path="SINGLE_PROVIDER",
                format_path="STRUCTURED_ONLY",
                raw_output=decision.to_dict(),
            ),
        )

    monkeypatch.setattr(
        MemoryLearningProvider,
        "select_with_evidence",
        deterministic_provider,
    )
    result = asyncio.run(
        MemoryService.learn_from_interaction(
            "h1a-user",
            "以后 Python 项目优先用 uv",
            scope="user",
            source_ref="run:h1a-entry",
        )
    )

    assert result == {"programming": {"python_environment": "uv"}}
    assert result.has_committed_evidence is True
    assert len(result.commit_evidence) == 1
    receipt = result.commit_evidence[0]
    assert receipt.committed is True
    assert receipt.canonical_key == "programming.python_environment"
    assert receipt.content == {"value": "uv"}
    assert long_term.get_fact(
        "h1a-user",
        "programming",
        "python_environment",
        scope="user",
    ) == "uv"


def test_runtime_does_not_acknowledge_an_uncommitted_preference(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from agent.memory import long_term
    from agent.runtime import UniversalAgent
    from agent.services.memory_service import ScopedMemoryView

    monkeypatch.setattr(long_term, "FACTS_DB_PATH", tmp_path / "facts.db")
    long_term._init_facts_db()

    async def ignore_preference(self, evidence, existing, policy):
        decision = MemoryLearningDecision.ignore("PROVIDER_IGNORED")
        return MemoryLearningSelection(
            decision=decision,
            evidence=MemoryLearningSelectionEvidence(
                provider="test-provider",
                provider_path="SINGLE_PROVIDER",
                format_path="STRUCTURED_ONLY",
                raw_output=decision.to_dict(),
            ),
        )

    monkeypatch.setattr(
        MemoryLearningProvider,
        "select_with_evidence",
        ignore_preference,
    )

    class Orchestrator:
        replan_count = 0

        def reset_timings(self) -> None:
            return None

        def get_timings(self) -> dict[str, float]:
            return {}

        async def plan(self, **_kwargs):
            return {
                "plan": [],
                "requested_outcomes": ["USER_VISIBLE_OUTPUT"],
                "answer_required": True,
            }, "FINISH", "好的，已记录你的偏好。"

        async def finalize(self, **kwargs) -> str:
            return str(kwargs.get("best_answer") or "好的，已记录你的偏好。")

    user_id = "h1a-runtime-no-commit"
    agent = UniversalAgent.__new__(UniversalAgent)
    agent._memory_view = ScopedMemoryView(user_id, learning_scope="user")
    agent._memory_namespace = user_id
    agent._pending_execution_target = ""
    agent._run_context = None
    agent._timings = {}
    agent._wall_total_seconds = 0.0
    agent._session_context = SimpleNamespace(
        conversation_tracker=SimpleNamespace(update=lambda **_kwargs: None),
        memory_scope="user",
    )
    agent.orchestrator = Orchestrator()
    monkeypatch.setattr(agent, "_print_timing_summary", lambda: {})

    answer = asyncio.run(
        agent._run_in_context("以后 Python 项目优先用 uv")
    )

    assert answer == "我理解了这项信息，但本次没有确认写入长期记忆。"
    assert long_term.get_facts(user_id, scope="user") == {}


def test_implicit_memory_intent_is_deterministic() -> None:
    for text in (
        "我平时 Python 项目偏好用什么？",
        "按我平常 Python 的方式来",
        "按照我的开发习惯初始化这个项目",
    ):
        intent, _target, _need, _outcomes = IntentEngine()._deterministic_analysis(
            CognitiveContext(query=text)
        )
        assert intent is not None, text
        assert intent.domain == DOMAIN_MEMORY, text
        assert intent.requires_execution is False, text


def _committed_result() -> MemoryLearningResult:
    return MemoryLearningResult(
        {"programming": {"python_environment": "uv"}},
        commits=(
            MemoryCommitEvidence(
                committed=True,
                action="STORE",
                store="user_facts",
                scope="user",
                canonical_key="programming.python_environment",
                evidence_id="evidence-h1a",
                reason_code="EXPLICIT_USER_PREFERENCE",
                record_id="1",
                revision=1,
                content={"value": "uv"},
            ),
        ),
    )


def test_memory_acknowledgement_requires_commit_evidence() -> None:
    answer = "好的，已记录你的偏好。"

    assert "已记录" not in _normalize_fact_capture_answer(
        answer,
        MemoryLearningResult(),
    )
    assert _normalize_fact_capture_answer(answer, _committed_result()) == answer
    assert _normalize_fact_capture_answer(
        "我无法确认或保存这条信息。",
        MemoryLearningResult(),
    ) == "我无法确认或保存这条信息。"


def test_noncanonical_result_cannot_prove_a_memory_commit() -> None:
    assert _normalize_fact_capture_answer(
        "我无法确认或保存这条信息。",
        {"personal": {"name": "小明"}},
    ) == "我无法确认或保存这条信息。"


def test_no_candidate_cannot_be_presented_as_durable_memory() -> None:
    assert _user_memory_candidates("我今天去了公园") == []
    assert _normalize_fact_capture_answer(
        "好的，我会记住这个。",
        MemoryLearningResult(),
    ) == "我理解了这项信息，但本次没有确认写入长期记忆。"
