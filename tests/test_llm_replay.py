"""Offline tests for the LLM cassette record/replay layer.

No Provider is contacted: record mode wraps injected fakes, replay mode
replaces the router methods entirely.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

import agent.llm as llm_module
from agent.execution_errors import ProviderError
from agent.llm import ChatTurn, LLMRouter, ToolCall
from realtest_reports.harness.llm_replay import (
    Cassette,
    ReplayMissError,
    canonical_messages,
    install,
    request_digest,
)


class _FakeProvider:
    """Deterministic stand-in serving canned responses per call."""

    model_name = "fake"
    model = "fake"
    base_url = "http://fake"
    openai_api_base = "http://fake"
    openai_api_key = "fake"
    timeout = 0.0
    request_timeout = 0.0

    def __init__(self, responses=(), errors=()):
        self._responses = list(responses) or ["canned"]
        self._errors = list(errors)
        self.calls = 0

    def _next(self):
        self.calls += 1
        if self._errors:
            raise self._errors.pop(0)
        if len(self._responses) > 1:
            return self._responses.pop(0)
        return self._responses[0]

    def invoke(self, messages, **_kwargs):
        return SimpleNamespace(
            content=self._next(), response_metadata={"provider": "fake"}
        )

    async def ainvoke(self, messages, **_kwargs):
        return SimpleNamespace(
            content=self._next(), response_metadata={"provider": "fake"}
        )

    async def achat(self, messages, tools, **_kwargs):
        return ChatTurn(content=self._next())

    def with_structured_output(self, schema, **_kwargs):
        parent = self

        class _Runnable:
            def invoke(self, messages, **_kw):
                return schema.model_validate({"value": parent._next()})

            async def ainvoke(self, messages, **_kw):
                return schema.model_validate({"value": parent._next()})

        return _Runnable()


class _Decision(BaseModel):
    value: str


def _router() -> LLMRouter:
    return LLMRouter(provider_mode="deepseek")


def test_record_then_replay_invoke(tmp_path):
    cassette = tmp_path / "c.json"
    router = _router()
    fake = _FakeProvider(responses=["first", "second"])
    router._deepseek = fake

    session = install("record", cassette, router=router)
    r1 = router.invoke([HumanMessage(content="你好")])
    r2 = asyncio.run(router.ainvoke([HumanMessage(content="继续")]))
    summary = session.close()

    assert r1.content == "first" and r2.content == "second"
    assert summary.recorded == 2 and cassette.exists()

    router2 = _router()
    replay = install("replay", cassette, router=router2)
    out1 = router2.invoke([HumanMessage(content="你好")])
    out2 = asyncio.run(router2.ainvoke([HumanMessage(content="继续")]))
    replayed = replay.close()

    assert out1.content == "first" and out2.content == "second"
    assert replayed.replayed == 2 and replayed.unconsumed == 0


def test_replay_never_touches_provider(tmp_path):
    cassette = tmp_path / "c.json"
    router = _router()
    router._deepseek = _FakeProvider()
    session = install("record", cassette, router=router)
    router.invoke([HumanMessage(content="hello")])
    session.close()

    router2 = _router()
    replay = install("replay", cassette, router=router2)
    # No provider injected at all: replay must answer on its own.
    out = router2.invoke([HumanMessage(content="hello")])
    replay.close()
    assert out.content == "canned"


def test_replay_miss_fails_fast(tmp_path):
    cassette = tmp_path / "c.json"
    router = _router()
    router._deepseek = _FakeProvider()
    session = install("record", cassette, router=router)
    router.invoke([HumanMessage(content="recorded")])
    session.close()

    router2 = _router()
    replay = install("replay", cassette, router=router2)
    # Misses travel the provider path: never retried, surfaced in the
    # ProviderError the router raises for its boundary failures.
    with pytest.raises(ProviderError, match="no cassette entry"):
        router2.invoke([HumanMessage(content="different request")])
    summary = replay.close()
    assert summary.misses and summary.misses[0]["kind"] == "invoke"


def test_duplicate_requests_replay_fifo(tmp_path):
    cassette = tmp_path / "c.json"
    router = _router()
    fake = _FakeProvider(responses=["one", "two"])
    router._deepseek = fake
    session = install("record", cassette, router=router)
    router.invoke([HumanMessage(content="same")])
    router.invoke([HumanMessage(content="same")])
    session.close()

    router2 = _router()
    replay = install("replay", cassette, router=router2)
    assert router2.invoke([HumanMessage(content="same")]).content == "one"
    assert router2.invoke([HumanMessage(content="same")]).content == "two"
    replay.close()


def test_time_hint_normalizes_in_digest():
    a = request_digest(
        "invoke",
        [
            SystemMessage(content="[当前时间: 2026-01-01 00:00:00]\n规则"),
            HumanMessage(content="hi"),
        ],
        None,
        {},
    )
    b = request_digest(
        "invoke",
        [
            SystemMessage(content="[当前时间: 2030-12-31 23:59:59]\n规则"),
            HumanMessage(content="hi"),
        ],
        None,
        {},
    )
    assert a == b


def test_all_prompt_time_injections_normalize():
    """Every wall-clock source reaching prompts must be digest-stable."""
    base = request_digest(
        "invoke",
        [
            SystemMessage(
                content="当前时间: 2026年11月03日 09:22 Tuesday\n规则"
            ),
            HumanMessage(
                content="当前时间：2026-11-03 09:22:27\n"
                "[2026-11-03T09:22] [1] 用户: 记住这个"
            ),
        ],
        None,
        {},
    )
    later = request_digest(
        "invoke",
        [
            SystemMessage(
                content="当前时间: 2027年01月15日 23:59 Friday\n规则"
            ),
            HumanMessage(
                content="当前时间：2027-01-15 23:59:59\n"
                "[2027-01-15T23:59] [1] 用户: 记住这个"
            ),
        ],
        None,
        {},
    )
    assert base == later


def test_recorded_provider_error_replays_as_error(tmp_path):
    cassette = tmp_path / "c.json"
    router = _router()
    router._deepseek = _FakeProvider(errors=[RuntimeError("HTTP 503 boom")])
    session = install("record", cassette, router=router)
    with pytest.raises(ProviderError, match="boom"):
        router.invoke([HumanMessage(content="x")])
    session.close()

    router2 = _router()
    replay = install("replay", cassette, router=router2)
    with pytest.raises(ProviderError, match="boom"):
        router2.invoke([HumanMessage(content="x")])
    replay.close()


def test_recorded_retry_failover_sequence_replays(tmp_path):
    """The CS01 regression: a recorded transient-error -> retry -> fallback
    -> success sequence must replay exactly, not stop at the first error."""
    cassette = tmp_path / "c.json"
    router = LLMRouter(provider_mode="auto")
    transient = ConnectionError("conn refused")
    router._deepseek = _FakeProvider(
        errors=[transient, transient, transient, transient]
    )
    router._ollama = _FakeProvider(responses=["from-ollama"])
    session = install("record", cassette, router=router)
    out = router.invoke([HumanMessage(content="hi")])
    session.close()
    assert out.content == "from-ollama"
    recorded = Cassette.load(cassette)
    assert len(recorded.entries) == 5  # 4 recorded errors + 1 success

    router2 = LLMRouter(provider_mode="auto")
    replay = install("replay", cassette, router=router2)
    out2 = router2.invoke([HumanMessage(content="hi")])
    summary = replay.close()
    assert out2.content == "from-ollama"
    assert summary.replayed == 5 and summary.unconsumed == 0


def test_achat_deepseek_seam_records_and_replays(tmp_path, monkeypatch):
    cassette = tmp_path / "c.json"
    calls = []

    async def fake_achat(provider, messages, tools, kwargs):
        calls.append(messages)
        return ChatTurn(
            content="done",
            tool_calls=(
                ToolCall(id="t1", name="read_file", arguments={"path": "a"}),
            ),
        )

    monkeypatch.setattr(llm_module, "_deepseek_achat", fake_achat)

    router = _router()
    router._deepseek = _FakeProvider()
    session = install("record", cassette, router=router)
    turn = asyncio.run(
        router.achat([{"role": "user", "content": "q"}], [])
    )
    session.close()
    assert turn.content == "done" and len(calls) == 1

    deltas = []
    router2 = _router()
    replay = install("replay", cassette, router=router2)
    turn2 = asyncio.run(
        router2.achat(
            [{"role": "user", "content": "q"}],
            [],
            on_delta=deltas.append,
        )
    )
    replay.close()
    assert turn2.content == "done"
    assert turn2.tool_calls[0].name == "read_file"
    assert turn2.tool_calls[0].arguments == {"path": "a"}
    assert deltas == ["done"]


def test_structured_output_path_roundtrips(tmp_path):
    cassette = tmp_path / "c.json"
    router = _router()
    router._deepseek = _FakeProvider(responses=["yes"])
    session = install("record", cassette, router=router)

    # Same boundary memory learning uses: provider obtained directly,
    # bypassing router.invoke/ainvoke.
    provider, name = router._get_active_provider()
    result = asyncio.run(
        provider.with_structured_output(_Decision).ainvoke(
            [HumanMessage(content="decide")]
        )
    )
    session.close()
    assert result.value == "yes" and name == "deepseek"

    router2 = _router()
    replay = install("replay", cassette, router=router2)
    provider2, _ = router2._get_active_provider()
    result2 = asyncio.run(
        provider2.with_structured_output(_Decision).ainvoke(
            [HumanMessage(content="decide")]
        )
    )
    replay.close()
    assert isinstance(result2, _Decision) and result2.value == "yes"


def test_canonical_message_projects_dict_and_langchain():
    assert canonical_messages(
        [{"role": "tool", "content": "out", "tool_call_id": "t9"}]
    )[0]["tool_call_id"] == "t9"
    projected = canonical_messages(
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "t1", "name": "shell", "arguments": {"cmd": "ls"}}
                ],
            }
        ]
    )[0]
    assert projected["tool_calls"][0]["name"] == "shell"


def test_cassette_rejects_wrong_version(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text('{"version": 99, "entries": []}', encoding="utf-8")
    with pytest.raises(ReplayMissError):
        Cassette.load(bad)


def test_close_restores_patches(tmp_path):
    cassette = tmp_path / "c.json"
    router = _router()
    original = router._get_active_provider
    session = install("record", cassette, router=router)
    assert router._get_active_provider is not original
    session.close()
    assert router._get_active_provider == original
    # Class-level property patches are restored too.
    assert type(router)._provider_deepseek is LLMRouter._provider_deepseek
