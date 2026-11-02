"""Phase 1: native tool calling in the LLM layer.

Both providers are exercised through mocked httpx responses: DeepSeek via the
OpenAI chat-completions schema, Ollama via its native /api/chat schema.
"""
from __future__ import annotations

import json

import asyncio
import httpx
import pytest

from agent.llm import (
    ChatTurn,
    LLMRouter,
    ToolCall,
    _openai_turn,
    _OllamaHTTPProvider,
    _parse_tool_arguments,
    _to_openai_messages,
    function_tool_spec,
)


class _FakeResponse:
    def __init__(self, payload, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    @property
    def is_error(self) -> bool:
        return self.status_code >= 400

    @property
    def text(self) -> str:
        return json.dumps(self._payload)

    def json(self):
        return self._payload


def test_openai_turn_parses_tool_calls():
    turn = _openai_turn({
        "choices": [{
            "message": {
                "content": None,
                "tool_calls": [{
                    "id": "call_abc",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": '{"path": "output/a.txt"}',
                    },
                }],
            }
        }]
    })
    assert turn.content == ""
    assert turn.tool_calls == (
        ToolCall(id="call_abc", name="read_file",
                 arguments={"path": "output/a.txt"}),
    )


def test_openai_turn_rejects_empty_choices():
    with pytest.raises(RuntimeError, match="no choices"):
        _openai_turn({"choices": []})


def test_parse_tool_arguments_degrades_visibly():
    assert _parse_tool_arguments({"a": 1}) == {"a": 1}
    assert "__invalid_arguments__" in _parse_tool_arguments("{not json")
    assert "__invalid_arguments__" in _parse_tool_arguments("[1,2]")
    assert _parse_tool_arguments(None) == {}


def test_to_openai_messages_renders_tool_transcript():
    rendered = _to_openai_messages([
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "c1", "name": "read_file",
                            "arguments": {"path": "a.py"}}],
        },
        {"role": "tool", "tool_call_id": "c1", "name": "read_file",
         "content": "data"},
    ])
    assert rendered[2]["tool_calls"][0]["function"] == {
        "name": "read_file",
        "arguments": '{"path": "a.py"}',
    }
    assert rendered[3] == {
        "role": "tool", "tool_call_id": "c1", "content": "data",
    }


def test_ollama_achat_sends_native_tools(monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, headers=None, json=None):
            captured["url"] = url
            captured["payload"] = json
            return _FakeResponse({
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "function": {
                            "name": "web_search",
                            "arguments": {"query": "news"},
                        }
                    }],
                }
            })

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    provider = _OllamaHTTPProvider(
        model="qwen3:8b", base_url="http://localhost:11434/v1", timeout=5
    )
    turn = asyncio.run(provider.achat(
        [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "查新闻"},
            {"role": "tool", "tool_call_id": "c9", "name": "read_file",
             "content": "x"},
        ],
        [function_tool_spec("web_search", "search", {"type": "object"})],
    ))
    assert captured["url"].endswith("/api/chat")
    payload = captured["payload"]
    assert payload["think"] is False
    assert payload["tools"][0]["function"]["name"] == "web_search"
    assert payload["messages"][2] == {
        "role": "tool", "tool_name": "read_file", "content": "x",
    }
    assert turn.tool_calls[0].name == "web_search"
    assert turn.tool_calls[0].id == "call-0"
    assert turn.tool_calls[0].arguments == {"query": "news"}


def test_deepseek_achat_posts_openai_schema(monkeypatch):
    from agent import llm as llm_module

    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, headers=None, json=None):
            captured["url"] = url
            captured["payload"] = json
            captured["headers"] = headers
            return _FakeResponse({
                "choices": [{
                    "message": {
                        "content": "done",
                        "tool_calls": [{
                            "id": "call_x",
                            "type": "function",
                            "function": {
                                "name": "write_file",
                                "arguments": {"path": "o.txt", "content": "1"},
                            },
                        }],
                    }
                }]
            })

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    class FakeDeepSeek:
        model_name = "deepseek-v4-flash"
        openai_api_base = "https://api.deepseek.com/v1"
        openai_api_key = "sk-test"
        timeout = 45

    turn = asyncio.run(llm_module._deepseek_achat(
        FakeDeepSeek(),
        [{"role": "user", "content": "写文件"}],
        [function_tool_spec("write_file", "write", {"type": "object"})],
        {"temperature": 0},
    ))
    assert captured["url"] == "https://api.deepseek.com/v1/chat/completions"
    assert captured["payload"]["tool_choice"] == "auto"
    assert captured["payload"]["tools"][0]["function"]["name"] == "write_file"
    assert turn.content == "done"
    assert turn.tool_calls == (
        ToolCall(id="call_x", name="write_file",
                 arguments={"path": "o.txt", "content": "1"}),
    )


def test_router_achat_falls_back_to_ollama(monkeypatch):
    from agent import llm as llm_module

    router = LLMRouter(provider_mode="auto")

    class FakeDeepSeek:
        timeout = 45

    class FakeOllama:
        timeout = 5

        async def achat(self, messages, tools, **kwargs):
            return ChatTurn(content="fallback", tool_calls=())

    router._deepseek = FakeDeepSeek()
    router._ollama = FakeOllama()

    async def failing_deepseek(provider, messages, tools, kwargs):
        raise RuntimeError("deepseek down")

    monkeypatch.setattr(llm_module, "_deepseek_achat", failing_deepseek)
    turn = asyncio.run(router.achat([{"role": "user", "content": "hi"}], []))
    assert turn.content == "fallback"


def test_router_ainvoke_still_works(monkeypatch):
    """_aexecute extraction must not break the plain text path."""
    router = LLMRouter(provider_mode="ollama")

    class FakeOllama:
        timeout = 1

        async def ainvoke(self, messages, **kwargs):
            return SimpleNamespaceLike("pong")

    class SimpleNamespaceLike:
        def __init__(self, content):
            self.content = content

    router._ollama = FakeOllama()
    result = asyncio.run(router.ainvoke([]))
    assert result.content == "pong"


def test_function_tool_spec_shape():
    spec = function_tool_spec(
        "shell", "run a command",
        {"type": "object", "properties": {"cmd": {"type": "string"}},
         "required": ["cmd"]},
    )
    assert spec == {
        "type": "function",
        "function": {
            "name": "shell",
            "description": "run a command",
            "parameters": {
                "type": "object",
                "properties": {"cmd": {"type": "string"}},
                "required": ["cmd"],
            },
        },
    }
