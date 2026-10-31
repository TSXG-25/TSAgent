"""Multi-turn context: model-visible history comes only from the durable run log."""

import asyncio
import json
from types import SimpleNamespace
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from agent.runtime import UniversalAgent


def test_runtime_passes_only_retrieved_memory_in_context(monkeypatch) -> None:
    """Runtime records nothing and retrieves memory only for memory requests."""
    retrieval_queries: list[str] = []

    class Memory:
        def get_retrieved_memory(self, query: str) -> dict[str, str]:
            retrieval_queries.append(query)
            return {"long_term": "lt", "facts": "f"}

    class Orchestrator:
        replan_count = 0

        def __init__(self) -> None:
            self.contexts: list[dict[str, Any]] = []

        def reset_timings(self) -> None:
            return None

        def get_timings(self) -> dict[str, float]:
            return {}

        async def plan(self, **kwargs: Any):
            self.contexts.append(kwargs["context"])
            return {
                "plan": [],
                "requested_outcomes": ["USER_VISIBLE_OUTPUT"],
                "answer_required": True,
            }, "FINISH", "answer"

        async def finalize(self, **kwargs: Any) -> str:
            return str(kwargs.get("best_answer") or "answer")

    def build_agent() -> tuple[UniversalAgent, Orchestrator]:
        orchestrator = Orchestrator()
        agent = UniversalAgent.__new__(UniversalAgent)
        agent._memory_view = Memory()
        agent._memory_namespace = "p0-multiturn-runtime"
        agent._pending_execution_target = ""
        agent._run_context = None
        agent._timings = {}
        agent._wall_total_seconds = 0.0
        agent.last_run_evidence = {}
        agent._session_context = SimpleNamespace(
            conversation_tracker=SimpleNamespace(update=lambda **_kwargs: None),
            memory_scope="session",
        )
        agent.orchestrator = orchestrator
        agent._print_timing_summary = lambda: {}
        return agent, orchestrator

    monkeypatch.setattr("agent.runtime._build_repo_context", lambda *_args: "")
    monkeypatch.setattr("agent.runtime.skill_registry.select", lambda _value: None)

    agent, orchestrator = build_agent()
    answer = asyncio.run(agent._run_in_context("把它改成红色背景"))

    assert answer == "answer"
    assert retrieval_queries == []
    (context,) = orchestrator.contexts
    assert context == {"runtime_pending_target": ""}


def _provider(monkeypatch, replies) -> Any:
    class RecordingProvider:
        supports_structured_output = False

        def __init__(self) -> None:
            self.replies = list(replies)
            self.calls: list[list] = []

        async def ainvoke(self, messages, **_kwargs):
            self.calls.append(list(messages))
            text = "\n".join(str(m.content) for m in messages)
            assert self.replies, f"Unexpected Provider call: {text[:500]}"
            required, reply = self.replies.pop(0)
            assert required in text, (required, text[:1000])
            return SimpleNamespace(
                content=json.dumps(reply, ensure_ascii=False)
                if isinstance(reply, dict)
                else reply
            )

    provider = RecordingProvider()
    monkeypatch.setattr("agent.llm.llm", provider)
    from agent.cognition.intent_engine import IntentEngine

    engine = IntentEngine()
    engine._llm = provider
    monkeypatch.setattr("agent.orchestrator.planner.intent_engine", engine)
    monkeypatch.setattr("agent.planner.planner.llm", provider)
    monkeypatch.setattr("agent.answer_generator.llm", provider)
    return provider


def _turn_two_history_assertions(calls: list[list], turn_one: tuple[str, str]) -> None:
    request, answer = turn_one
    answer_call = next(
        call
        for call in reversed(calls)
        if any(isinstance(m, HumanMessage) and m.content == "总结一下上面的内容" for m in call)
    )
    assert any(
        isinstance(m, HumanMessage) and m.content == request for m in answer_call
    )
    assert any(
        isinstance(m, AIMessage) and answer in m.content for m in answer_call
    )
    current_index = next(
        i
        for i, m in enumerate(answer_call)
        if isinstance(m, HumanMessage) and m.content == "总结一下上面的内容"
    )
    history_index = next(
        i
        for i, m in enumerate(answer_call)
        if isinstance(m, HumanMessage) and m.content == request
    )
    assert history_index < current_index


def test_agent_service_second_turn_sees_first_turn(monkeypatch, tmp_path) -> None:
    """Turn 2 answer prompt carries turn 1 as real history messages."""
    from tests.test_ha_r1_harness import intent, run_turn, service_at

    follow_up = "总结一下上面的内容"
    provider = _provider(
        monkeypatch,
        [
            ("你好", intent("你好", domain="闲聊", action="answer")),
        ("你好", "你好！我是助手。"),
            (follow_up, intent(follow_up, domain="闲聊", action="answer")),
            (follow_up, "这是对上文的总结。"),
        ],
    )

    async def scenario() -> None:
        service, _store = service_at(tmp_path)
        try:
            first, _events = await run_turn(
                service, "你好", 1, session="p0-multiturn-service"
            )
            assert first.status.value == "COMPLETED", first
            second, _events = await run_turn(
                service, follow_up, 2, session="p0-multiturn-service"
            )
            assert second.status.value == "COMPLETED", second
        finally:
            await service.close()

    asyncio.run(scenario())
    assert not provider.replies
    _turn_two_history_assertions(provider.calls, ("你好", "你好！我是助手。"))


def test_conversation_history_survives_service_restart(monkeypatch, tmp_path) -> None:
    """A new AgentService on the same SQLite file sees turn 1: the durable
    run log is the history source, not an in-process buffer."""
    from tests.test_ha_r1_harness import intent, run_turn

    follow_up = "总结一下上面的内容"
    provider = _provider(
        monkeypatch,
        [
            ("你好", intent("你好", domain="闲聊", action="answer")),
        ("你好", "你好！我是助手。"),
            (follow_up, intent(follow_up, domain="闲聊", action="answer")),
            (follow_up, "这是对上文的总结。"),
        ],
    )
    database = tmp_path / "runtime.sqlite"

    async def scenario() -> None:
        from agent.runtime_store import SqliteRuntimeStore
        from agent.service import AgentService
        from agent.service.context_factory import ServiceContextFactory
        from agent.service.runtime_launcher import RuntimeExecutionLauncher

        store_a = SqliteRuntimeStore.open(database)
        service_a = AgentService(
            runtime_store=store_a,
            launcher=RuntimeExecutionLauncher(),
            context_factory=ServiceContextFactory(store_a, workspace_root=tmp_path),
        )
        try:
            first, _events = await run_turn(
                service_a, "你好", 1, session="p0-restart-service"
            )
            assert first.status.value == "COMPLETED", first
        finally:
            await service_a.close()

        store_b = SqliteRuntimeStore.open(database)
        service_b = AgentService(
            runtime_store=store_b,
            launcher=RuntimeExecutionLauncher(),
            context_factory=ServiceContextFactory(store_b, workspace_root=tmp_path),
        )
        try:
            second, _events = await run_turn(
                service_b, follow_up, 2, session="p0-restart-service"
            )
            assert second.status.value == "COMPLETED", second
        finally:
            await service_b.close()

    asyncio.run(scenario())
    assert not provider.replies
    _turn_two_history_assertions(provider.calls, ("你好", "你好！我是助手。"))
