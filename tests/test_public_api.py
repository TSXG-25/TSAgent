"""Contract tests for the stable TSAgent facade (AgentService boundary)."""
import asyncio
import json
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage


def _intent(request):
    return {
        "domain": "闲聊", "action": "chat", "target": "", "entities": [],
        "confidence": 0.99, "requires_execution": False, "summary": request,
        "resolved_request": request, "conversation_relation": "new_request",
        "requested_outcomes": ["USER_VISIBLE_OUTPUT"], "artifact_required": False,
        "delivery_requirements": [], "prohibited_effects": [],
        "acceptance_criteria": [], "freshness_required": False,
        "source_grounding_required": False,
    }


def _provider(monkeypatch, replies):
    class RecordingProvider:
        supports_structured_output = False

        def __init__(self):
            self.replies = list(replies)
            self.calls = []

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
    monkeypatch.setattr("agent.answer_generator.llm", provider)
    return provider


def test_tsagent_run_returns_answer_and_rejects_empty(monkeypatch, tmp_path):
    from agent import TSAgent

    provider = _provider(monkeypatch, [("你好", _intent("你好")), ("你好", "你好！我是助手。")])

    async def scenario():
        agent = TSAgent(
            "public-api",
            database_path=tmp_path / "runtime.sqlite",
            workspace_root=tmp_path,
            session_id="session-a",
        )
        try:
            assert agent.user_id == "public-api"
            assert agent.tenant_id == "default"
            assert agent.session_id == "session-a"
            assert await agent.run("你好") == "你好！我是助手。"
            try:
                await agent.run("  ")
            except ValueError as exc:
                assert "non-empty" in str(exc)
            else:
                raise AssertionError("empty input should be rejected")
        finally:
            await agent.close()

    asyncio.run(scenario())
    assert not provider.replies


def test_tsagent_history_crosses_facade_instances(monkeypatch, tmp_path):
    from agent import TSAgent

    db = tmp_path / "runtime.sqlite"
    provider = _provider(
        monkeypatch,
        [
            ("你好", _intent("你好")),
            ("你好", "你好！我是助手。"),
            ("谢谢", _intent("谢谢")),
            ("谢谢", "不客气。"),
        ],
    )

    async def scenario():
        first = TSAgent(
            "public-api",
            database_path=db,
            workspace_root=tmp_path,
            session_id="session-a",
        )
        try:
            assert await first.run("你好") == "你好！我是助手。"
        finally:
            await first.close()

        second = TSAgent(
            "public-api",
            database_path=db,
            workspace_root=tmp_path,
            session_id="session-a",
        )
        try:
            assert await second.run("谢谢") == "不客气。"
        finally:
            await second.close()

    asyncio.run(scenario())
    assert not provider.replies

    # calls: intent, answer (turn 1), intent, answer (turn 2)
    second_call = provider.calls[3]
    assert any(
        isinstance(m, HumanMessage) and m.content == "你好" for m in second_call
    )
    assert any(
        isinstance(m, AIMessage) and "你好！我是助手。" in m.content
        for m in second_call
    )


def test_tsagent_sessions_are_isolated_on_one_database(monkeypatch, tmp_path):
    from agent import TSAgent

    db = tmp_path / "runtime.sqlite"
    provider = _provider(
        monkeypatch,
        [
            ("你好", _intent("你好")),
            ("你好", "你好！我是助手。"),
            ("谢谢", _intent("谢谢")),
            ("谢谢", "不客气。"),
        ],
    )

    async def scenario():
        first = TSAgent(
            "public-api",
            database_path=db,
            workspace_root=tmp_path,
            session_id="session-a",
        )
        try:
            assert await first.run("你好") == "你好！我是助手。"
        finally:
            await first.close()

        other = TSAgent(
            "public-api",
            database_path=db,
            workspace_root=tmp_path,
            session_id="session-b",
        )
        try:
            assert await other.run("谢谢") == "不客气。"
        finally:
            await other.close()

    asyncio.run(scenario())
    assert not provider.replies

    second_call = provider.calls[3]
    assert not any(
        isinstance(m, (HumanMessage, AIMessage)) and "你好" in str(m.content)
        for m in second_call
    )
