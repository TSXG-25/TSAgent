from __future__ import annotations

import asyncio
import time

import pytest

from agent.llm import LLMRouter, _OllamaHTTPProvider
from agent.execution_errors import ProviderError
from langchain_core.messages import HumanMessage, SystemMessage


class _Provider:
    def __init__(
        self,
        name: str,
        *,
        error: Exception | None = None,
        model: str = "",
        base_url: str = "",
    ) -> None:
        self.name = name
        self.error = error
        self.calls = 0
        self.model = model
        self.base_url = base_url

    async def ainvoke(self, _messages, **_kwargs):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return f"{self.name}-result"


class _EmptyError(Exception):
    def __str__(self) -> str:
        return ""


def test_provider_timeout_preserves_structured_category() -> None:
    router = LLMRouter(provider_mode="deepseek")
    router._deepseek = _Provider("deepseek", error=TimeoutError())
    with pytest.raises(ProviderError) as caught:
        asyncio.run(router.ainvoke([]))
    assert caught.value.code == "PROVIDER_TIMEOUT"
    assert isinstance(caught.value.__cause__, TimeoutError)


def test_empty_provider_errors_have_a_non_empty_diagnostic() -> None:
    router = LLMRouter(provider_mode="ollama")
    provider = _Provider("ollama", error=_EmptyError())
    router._ollama = provider  # type: ignore[assignment]

    with pytest.raises(RuntimeError) as caught:
        asyncio.run(router.ainvoke([]))

    message = str(caught.value)
    assert "指定的 LLM Provider 不可用 (ollama)" in message
    assert "EmptyError: ollama request failed" in message
    assert router.status["last_error"] == "_EmptyError: ollama request failed"


def test_strict_ollama_mode_does_not_fallback_to_deepseek() -> None:
    router = LLMRouter(provider_mode="ollama")
    ollama = _Provider("ollama", error=ConnectionError("ollama unavailable"))
    deepseek = _Provider("deepseek")
    router._ollama = ollama  # type: ignore[assignment]
    router._deepseek = deepseek  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match=r"指定的 LLM Provider 不可用 \(ollama\)"):
        asyncio.run(router.ainvoke([]))

    assert ollama.calls == 1
    assert deepseek.calls == 0


def test_auto_mode_keeps_provider_fallback() -> None:
    router = LLMRouter(provider_mode="auto")
    deepseek = _Provider("deepseek", error=ConnectionError("deepseek unavailable"))
    ollama = _Provider("ollama")
    router._deepseek = deepseek  # type: ignore[assignment]
    router._ollama = ollama  # type: ignore[assignment]

    assert asyncio.run(router.ainvoke([])) == "ollama-result"
    assert deepseek.calls == 1
    assert ollama.calls == 1


def test_auto_mode_cools_down_after_both_providers_fail() -> None:
    router = LLMRouter(provider_mode="auto")
    deepseek = _Provider("deepseek", error=ConnectionError("deepseek unavailable"))
    ollama = _Provider("ollama", error=ConnectionError("ollama unavailable"))
    router._deepseek = deepseek  # type: ignore[assignment]
    router._ollama = ollama  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="所有 LLM 提供商均不可用"):
        asyncio.run(router.ainvoke([]))
    assert (deepseek.calls, ollama.calls) == (1, 1)

    # A planner/replan retry must fail locally during the cooldown instead of
    # paying the same two provider waits again.
    with pytest.raises(RuntimeError, match="暂时不可用"):
        asyncio.run(router.ainvoke([]))
    assert (deepseek.calls, ollama.calls) == (1, 1)

    router._providers_unavailable_until = time.monotonic() - 1
    with pytest.raises(RuntimeError, match="暂时不可用"):
        asyncio.run(router.ainvoke([]))
    assert (deepseek.calls, ollama.calls) == (2, 1)


def test_provider_mode_rejects_unknown_value() -> None:
    with pytest.raises(ValueError, match="TSAGENT_LLM_PROVIDER"):
        LLMRouter(provider_mode="qwen")


def test_structured_output_disable_is_scoped_to_provider_endpoint(monkeypatch) -> None:
    router = LLMRouter(provider_mode="auto")

    first = _Provider(
        "first",
        model="model-a",
        base_url="https://provider-a.example/v1",
    )
    second = _Provider(
        "second",
        model="model-b",
        base_url="https://provider-b.example/v1",
    )
    active = [first, "provider-a"]

    monkeypatch.setattr(router, "_get_active_provider", lambda: tuple(active))

    assert router.supports_structured_output is True
    router.disable_structured_output()
    assert router.supports_structured_output is False

    active[:] = [second, "provider-b"]
    assert router.supports_structured_output is True


def test_ollama_http_provider_builds_openai_compatible_messages() -> None:
    provider = _OllamaHTTPProvider(
        model="qwen2.5:14b",
        base_url="http://127.0.0.1:11434/v1/",
        timeout=5,
    )

    payload = provider._payload(
        [SystemMessage(content="system"), HumanMessage(content="user")],
        {},
    )

    assert payload["model"] == "qwen2.5:14b"
    assert payload["messages"] == [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "user"},
    ]


def test_ollama_http_provider_bypasses_system_proxy(monkeypatch) -> None:
    import httpx

    clients: list[dict[str, object]] = []

    class Response:
        is_error = False

        @staticmethod
        def json() -> dict:
            return {"choices": [{"message": {"content": "ok"}}]}

    class Client:
        def __init__(self, **kwargs: object) -> None:
            clients.append(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def post(self, *_args: object, **_kwargs: object) -> Response:
            return Response()

    class AsyncClient(Client):
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, *_args: object, **_kwargs: object) -> Response:
            return Response()

    monkeypatch.setattr(httpx, "Client", Client)
    monkeypatch.setattr(httpx, "AsyncClient", AsyncClient)
    provider = _OllamaHTTPProvider(
        model="qwen2.5:7b",
        base_url="http://localhost:11434/v1",
        timeout=5,
    )

    assert provider.invoke([]).content == "ok"
    assert asyncio.run(provider.ainvoke([])).content == "ok"
    assert clients == [
        {"timeout": 5, "trust_env": False},
        {"timeout": 5, "trust_env": False},
    ]
