"""LLM 抽象层 — 主用 DeepSeek，失败自动降级到本地 Ollama。

提供与之前一致的 `llm.ainvoke(messages)` 接口，所有使用者无需修改。
同时自动在所有请求中注入当前时间信息。
"""
import asyncio
import json
import os
import logging
import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, Final, Mapping, Protocol, Sequence
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

from langchain_core.messages import SystemMessage, BaseMessage
from agent.interruption import RunInterruptionRequested, await_interruptibly
from agent.execution_errors import (
    ProviderError,
    classify_execution_error,
    stable_error_message,
)

logger = logging.getLogger(__name__)


# --- Native tool calling ---
# Canonical transcript messages are plain dicts so they serialize into the
# durable AgentState unchanged:
#   {"role": "system"|"user"|"assistant", "content": str}
#   {"role": "assistant", "content": str,
#    "tool_calls": [{"id": str, "name": str, "arguments": dict}]}
#   {"role": "tool", "tool_call_id": str, "name": str, "content": str}


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ChatTurn:
    """One assistant turn in the tool-calling loop."""

    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()


def function_tool_spec(
    name: str, description: str, parameters: Mapping[str, Any]
) -> dict[str, Any]:
    """OpenAI-compatible tool spec; Ollama accepts the same shape."""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": dict(parameters),
        },
    }


def _to_openai_messages(messages: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Render canonical transcript dicts as OpenAI chat-completion messages."""
    rendered: list[dict[str, Any]] = []
    for message in messages:
        role = str(message.get("role", "user"))
        if role == "tool":
            rendered.append({
                "role": "tool",
                "tool_call_id": str(message.get("tool_call_id", "")),
                "content": str(message.get("content", "")),
            })
            continue
        entry: dict[str, Any] = {
            "role": role,
            "content": str(message.get("content", "")),
        }
        calls = message.get("tool_calls") or ()
        if calls:
            entry["tool_calls"] = [
                {
                    "id": str(call.get("id", "")),
                    "type": "function",
                    "function": {
                        "name": str(call.get("name", "")),
                        "arguments": json.dumps(
                            call.get("arguments") or {}, ensure_ascii=False
                        ),
                    },
                }
                for call in calls
            ]
        rendered.append(entry)
    return rendered


def _parse_tool_arguments(raw: Any) -> dict[str, Any]:
    """Parse provider-returned arguments; malformed JSON degrades visibly."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {"__invalid_arguments__": raw[:500]}
        return parsed if isinstance(parsed, dict) else {"__invalid_arguments__": raw[:500]}
    return {}


def _openai_turn(data: Mapping[str, Any]) -> ChatTurn:
    choices = data.get("choices") if isinstance(data, Mapping) else None
    if not choices:
        raise RuntimeError("chat completion returned no choices")
    message = choices[0].get("message", {}) if isinstance(choices[0], Mapping) else {}
    if not isinstance(message, Mapping):
        raise RuntimeError("chat completion returned a malformed message")
    content = message.get("content")
    tool_calls = tuple(
        ToolCall(
            id=str(call.get("id", f"call-{index}")),
            name=str((call.get("function") or {}).get("name", "")),
            arguments=_parse_tool_arguments(
                (call.get("function") or {}).get("arguments")
            ),
        )
        for index, call in enumerate(message.get("tool_calls") or ())
    )
    return ChatTurn(content=content if isinstance(content, str) else "", tool_calls=tool_calls)

# --- Configuration ---
DEEPSEEK_API_KEY = os.getenv("OPENAI_API_KEY", "")
DEEPSEEK_MODEL = os.getenv("MODEL_NAME", "deepseek-v4-flash")
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"

# Ollama (local fallback)
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:8b")
LLM_REQUEST_TIMEOUT: Final[float] = float(os.getenv("TSAGENT_LLM_TIMEOUT", "120"))
OLLAMA_REQUEST_TIMEOUT: Final[float] = float(os.getenv("TSAGENT_OLLAMA_TIMEOUT", "120"))
LLM_PROVIDER_COOLDOWN: Final[float] = float(
    os.getenv("TSAGENT_LLM_PROVIDER_COOLDOWN", "5")
)
LLM_PROVIDER_RETRY_AFTER: Final[float] = float(
    os.getenv("TSAGENT_LLM_PROVIDER_RETRY_AFTER", "60")
)
LLM_PROVIDER_MAX_RETRIES: Final[int] = int(
    os.getenv("TSAGENT_LLM_PROVIDER_RETRIES", "3")
)
_VALID_PROVIDER_MODES = {"auto", "deepseek", "ollama"}


class _Provider(Protocol):
    def invoke(self, messages: list[BaseMessage], **kwargs: object) -> Any: ...

    async def ainvoke(self, messages: list[BaseMessage], **kwargs: object) -> Any: ...

    async def achat(
        self,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
        **kwargs: object,
    ) -> ChatTurn: ...

    def with_structured_output(self, *args: object, **kwargs: object) -> Any: ...


class _OllamaHTTPProvider:
    """Small OpenAI-compatible Ollama adapter for the raw text path.

    Importing ``langchain_openai.ChatOpenAI`` pulls the optional transformer
    and torch stack through ``LanguageModelInput``. Ollama is already marked
    as not supporting structured output, so the Runtime only needs the stable
    text ``invoke``/``ainvoke`` contract here.
    """

    def __init__(self, *, model: str, base_url: str, timeout: float) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        # Native Ollama endpoints live off the host root, not under the
        # OpenAI-compatible /v1 prefix the base URL carries.
        self._chat_url = (
            self.base_url.removesuffix("/v1") + "/api/chat"
        )
        self.timeout = timeout

    @staticmethod
    def _messages(messages: list[BaseMessage]) -> list[dict[str, object]]:
        roles = {
            "human": "user",
            "ai": "assistant",
            "system": "system",
            "tool": "tool",
        }
        result: list[dict[str, object]] = []
        for message in messages:
            if isinstance(message, dict):
                message_type = str(message.get("role", "user"))
                content = message.get("content", "")
            else:
                message_type = str(getattr(message, "type", "user"))
                content = getattr(message, "content", "")
            result.append({
                "role": roles.get(message_type, message_type),
                "content": content,
            })
        return result

    @staticmethod
    def _chat_messages(
        messages: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, object]]:
        """Render canonical transcript dicts for the native /api/chat schema."""
        rendered: list[dict[str, object]] = []
        for message in messages:
            role = str(message.get("role", "user"))
            if role == "tool":
                rendered.append({
                    "role": "tool",
                    "tool_name": str(message.get("name", "")),
                    "content": str(message.get("content", "")),
                })
                continue
            entry: dict[str, object] = {
                "role": role,
                "content": str(message.get("content", "")),
            }
            calls = message.get("tool_calls") or ()
            if calls:
                entry["tool_calls"] = [
                    {
                        "type": "function",
                        "function": {
                            "name": str(call.get("name", "")),
                            "arguments": dict(call.get("arguments") or {}),
                        },
                    }
                    for call in calls
                ]
            rendered.append(entry)
        return rendered

    def _chat_payload(
        self,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
        kwargs: dict[str, object],
    ) -> dict[str, object]:
        options: dict[str, object] = {"temperature": kwargs.get("temperature", 0)}
        if "max_tokens" in kwargs:
            options["num_predict"] = kwargs["max_tokens"]
        return {
            "model": self.model,
            "messages": self._chat_messages(messages),
            "tools": [dict(tool) for tool in tools],
            "stream": False,
            "think": False,
            "options": options,
        }

    async def achat(
        self,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
        on_delta: Any = None,
        **kwargs: object,
    ) -> ChatTurn:
        import httpx

        payload = self._chat_payload(messages, tools, kwargs)
        if callable(on_delta):
            payload["stream"] = True
        async with httpx.AsyncClient(
            timeout=self.timeout,
            trust_env=False,
        ) as client:
            if not callable(on_delta):
                response = await client.post(
                    self._chat_url,
                    headers={"Authorization": "Bearer ollama"},
                    json=payload,
                )
            else:
                async with client.stream(
                    "POST",
                    self._chat_url,
                    headers={"Authorization": "Bearer ollama"},
                    json=payload,
                ) as stream_response:
                    if stream_response.is_error:
                        await stream_response.aread()
                        raise RuntimeError(
                            f"Ollama HTTP {stream_response.status_code}: "
                            f"{stream_response.text[:200]}"
                        )
                    content_parts: list[str] = []
                    tool_calls: tuple[ToolCall, ...] = ()
                    async for line in stream_response.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            chunk = json.loads(line)
                        except ValueError:
                            continue
                        message = chunk.get("message") or {}
                        text = message.get("content")
                        if isinstance(text, str) and text:
                            content_parts.append(text)
                            on_delta(text)
                        calls = message.get("tool_calls")
                        if calls:
                            tool_calls = tuple(
                                ToolCall(
                                    id=str(call.get("id", f"call-{index}")),
                                    name=str(
                                        (call.get("function") or {}).get("name", "")
                                    ),
                                    arguments=_parse_tool_arguments(
                                        (call.get("function") or {}).get("arguments")
                                    ),
                                )
                                for index, call in enumerate(calls)
                            )
                    return ChatTurn(
                        content="".join(content_parts), tool_calls=tool_calls
                    )
        if response.is_error:
            raise RuntimeError(
                f"Ollama HTTP {response.status_code}: {response.text[:200]}"
            )
        data = response.json()
        message = data.get("message", {}) if isinstance(data, dict) else {}
        if not isinstance(message, dict):
            raise RuntimeError("Ollama returned a malformed chat message")
        content = message.get("content")
        tool_calls = tuple(
            ToolCall(
                id=str(call.get("id", f"call-{index}")),
                name=str((call.get("function") or {}).get("name", "")),
                arguments=_parse_tool_arguments(
                    (call.get("function") or {}).get("arguments")
                ),
            )
            for index, call in enumerate(message.get("tool_calls") or ())
        )
        if not isinstance(content, str) and not tool_calls:
            raise RuntimeError("Ollama returned an empty chat completion")
        return ChatTurn(content=content if isinstance(content, str) else "", tool_calls=tool_calls)

    def _payload(
        self,
        messages: list[BaseMessage],
        kwargs: dict[str, object],
    ) -> dict[str, object]:
        options: dict[str, object] = {"temperature": kwargs.get("temperature", 0)}
        if "max_tokens" in kwargs:
            options["num_predict"] = kwargs["max_tokens"]
        for key in ("top_p", "stop"):
            if key in kwargs:
                options[key] = kwargs[key]
        return {
            "model": self.model,
            "messages": self._messages(messages),
            "stream": False,
            # Ollama's native endpoint is the only one that honours
            # think:false; the OpenAI shim ignores it and qwen3 spends
            # most of its latency on the reasoning trace.
            "think": False,
            "options": options,
        }

    @staticmethod
    def _response(response: Any) -> SimpleNamespace:
        data = response.json()
        message = data.get("message", {}) if isinstance(data, dict) else {}
        content = message.get("content", "") if isinstance(message, dict) else ""
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("Ollama returned an empty chat completion")
        return SimpleNamespace(content=content, response_metadata=data)

    async def ainvoke(
        self,
        messages: list[BaseMessage],
        **kwargs: object,
    ) -> SimpleNamespace:
        import httpx

        # Ollama is a loopback service.  ``httpx`` otherwise inherits macOS
        # system proxy settings and may send localhost traffic to an HTTP
        # proxy, producing a delayed blank 503 instead of reaching Ollama.
        async with httpx.AsyncClient(
            timeout=self.timeout,
            trust_env=False,
        ) as client:
            response = await client.post(
                self._chat_url,
                headers={"Authorization": "Bearer ollama"},
                json=self._payload(messages, kwargs),
            )
        if response.is_error:
            raise RuntimeError(
                f"Ollama HTTP {response.status_code}: {response.text[:200]}"
            )
        return self._response(response)

    def invoke(
        self,
        messages: list[BaseMessage],
        **kwargs: object,
    ) -> SimpleNamespace:
        import httpx

        with httpx.Client(
            timeout=self.timeout,
            trust_env=False,
        ) as client:
            response = client.post(
                self._chat_url,
                headers={"Authorization": "Bearer ollama"},
                json=self._payload(messages, kwargs),
            )
        if response.is_error:
            raise RuntimeError(
                f"Ollama HTTP {response.status_code}: {response.text[:200]}"
            )
        return self._response(response)

    def with_structured_output(self, *_args: object, **_kwargs: object) -> object:
        raise RuntimeError("Ollama structured output is disabled")


async def _deepseek_achat(
    provider: Any,
    messages: Sequence[Mapping[str, Any]],
    tools: Sequence[Mapping[str, Any]],
    kwargs: dict[str, object],
) -> ChatTurn:
    """Native OpenAI-compatible tool calling against the DeepSeek endpoint."""
    import httpx

    on_delta = kwargs.pop("on_delta", None)
    payload: dict[str, Any] = {
        "model": getattr(provider, "model_name", None) or DEEPSEEK_MODEL,
        "messages": _to_openai_messages(messages),
        "tools": [dict(tool) for tool in tools],
        "tool_choice": "auto",
        "temperature": kwargs.get("temperature", 0),
        "stream": callable(on_delta),
    }
    if "max_tokens" in kwargs:
        payload["max_tokens"] = kwargs["max_tokens"]
    api_key = getattr(provider, "openai_api_key", None) or DEEPSEEK_API_KEY
    # LangChain stores the key as SecretStr; str() renders the '**********'
    # mask, which the API then rejects as an invalid key.
    get_secret = getattr(api_key, "get_secret_value", None)
    if callable(get_secret):
        api_key = get_secret()
    api_key = str(api_key)
    base_url = (
        getattr(provider, "openai_api_base", None) or DEEPSEEK_BASE_URL
    ).rstrip("/")
    async with httpx.AsyncClient(timeout=_request_timeout(provider)) as client:
        if not callable(on_delta):
            response = await client.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
            )
            if response.is_error:
                raise RuntimeError(
                    f"DeepSeek HTTP {response.status_code}: {response.text[:200]}"
                )
            return _openai_turn(response.json())
        async with client.stream(
            "POST",
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
        ) as response:
            if response.is_error:
                await response.aread()
                raise RuntimeError(
                    f"DeepSeek HTTP {response.status_code}: {response.text[:200]}"
                )
            content_parts: list[str] = []
            tool_acc: dict[int, dict[str, str]] = {}
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except ValueError:
                    continue
                for choice in chunk.get("choices") or ():
                    delta = choice.get("delta") or {}
                    text = delta.get("content")
                    if isinstance(text, str) and text:
                        content_parts.append(text)
                        on_delta(text)
                    for call in delta.get("tool_calls") or ():
                        acc = tool_acc.setdefault(
                            int(call.get("index", 0)),
                            {"id": "", "name": "", "arguments": ""},
                        )
                        function = call.get("function") or {}
                        acc["id"] = str(call.get("id") or acc["id"])
                        acc["name"] = str(function.get("name") or acc["name"])
                        acc["arguments"] += str(function.get("arguments") or "")
            return ChatTurn(
                content="".join(content_parts),
                tool_calls=tuple(
                    ToolCall(
                        id=acc["id"] or f"call-{index}",
                        name=acc["name"],
                        arguments=_parse_tool_arguments(acc["arguments"]),
                    )
                    for index, acc in sorted(tool_acc.items())
                ),
            )


def _inject_time_to_transcript(
    messages: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Apply the same current-time hint to canonical transcript dicts."""
    now_hint = f"[当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]"
    rendered = [dict(message) for message in messages]
    for index, message in enumerate(rendered):
        if message.get("role") == "system":
            content = str(message.get("content", ""))
            if "[当前时间:" in content:
                import re
                content = re.sub(r"\[当前时间: [^\]]+\]", now_hint, content)
            else:
                content = f"{now_hint}\n{content}"
            rendered[index] = {**message, "content": content}
            return rendered
    return [{"role": "system", "content": now_hint}] + rendered


def _inject_time_to_system(messages: list[BaseMessage]) -> list[BaseMessage]:
    now_hint = f"[当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]"
    if not messages:
        return [SystemMessage(content=now_hint)]
    for i, msg in enumerate(messages):
        if isinstance(msg, SystemMessage):
            content = msg.content
            if "[当前时间:" in content:
                import re
                content = re.sub(r'\[当前时间: [^\]]+\]', now_hint, content)
            else:
                content = f"{now_hint}\n{content}"
            messages[i] = SystemMessage(content=content)
            return messages
    return [SystemMessage(content=now_hint)] + messages


def _request_timeout(provider) -> float:
    return float(
        getattr(provider, "timeout", 0)
        or getattr(provider, "request_timeout", 0)
        or LLM_REQUEST_TIMEOUT
    )


def _retryable_provider_error(error: object) -> bool:
    """Transient connectivity failures are worth retrying before fallback;
    request-level errors (4xx/auth/validation) fail immediately."""
    code = classify_execution_error(error)
    if code in ("PROVIDER_TIMEOUT", "PROVIDER_NETWORK"):
        return True
    if code:
        return False
    text = str(error or "").upper()
    if "HTTP 4" in text or "HTTP 5" in text:
        return False
    return True


class LLMRouter:
    def __init__(self, *, provider_mode: str | None = None):
        raw_mode = (
            provider_mode
            if provider_mode is not None
            else os.getenv("TSAGENT_LLM_PROVIDER", "auto")
        )
        configured_mode = raw_mode.strip().lower()
        if configured_mode not in _VALID_PROVIDER_MODES:
            raise ValueError(
                "TSAGENT_LLM_PROVIDER must be one of: auto, deepseek, ollama"
            )
        self._provider_mode = configured_mode
        self._deepseek = None
        self._ollama = None
        self._deepseek_available = True
        self._ollama_available = True
        self._fallback_count = 0
        self._last_error = ""
        self._providers_unavailable_until = 0.0
        self._deepseek_retry_at = 0.0
        # Structured-output support belongs to a concrete provider endpoint.
        # A DeepSeek response-format rejection must not poison a later model
        # or endpoint selected by the same router.
        self._structured_output_capabilities: dict[tuple[str, str, str], bool] = {}

    @property
    def supports_structured_output(self) -> bool:
        """Return structured-output support for the active provider endpoint."""
        provider, name = self._get_active_provider()
        key = self._structured_output_key(provider, name)
        if key not in self._structured_output_capabilities:
            # The local adapter intentionally uses the raw JSON path. Other
            # providers are probed by the caller and cached only on failure.
            self._structured_output_capabilities[key] = name != "ollama"
        return self._structured_output_capabilities[key]

    def disable_structured_output(self):
        """Disable structured output for the currently active endpoint."""
        provider, name = self._get_active_provider()
        self._structured_output_capabilities[
            self._structured_output_key(provider, name)
        ] = False

    @staticmethod
    def _structured_output_key(
        provider: _Provider,
        provider_name: str,
    ) -> tuple[str, str, str]:
        """Identify the endpoint whose structured capability was observed."""
        model = getattr(provider, "model", None) or getattr(
            provider, "model_name", ""
        )
        base_url = getattr(provider, "base_url", None) or getattr(
            provider, "openai_api_base", ""
        )
        if provider_name == "deepseek":
            model = model or DEEPSEEK_MODEL
            base_url = base_url or DEEPSEEK_BASE_URL
        elif provider_name == "ollama":
            model = model or OLLAMA_MODEL
            base_url = base_url or OLLAMA_BASE_URL
        return (provider_name, str(model), str(base_url).rstrip("/"))

    @property
    def _provider_deepseek(self):
        if self._deepseek is None:
            from langchain_openai import ChatOpenAI

            self._deepseek = ChatOpenAI(
                model=DEEPSEEK_MODEL,
                openai_api_key=DEEPSEEK_API_KEY,
                openai_api_base=DEEPSEEK_BASE_URL,
                temperature=0,
                timeout=LLM_REQUEST_TIMEOUT,
                max_retries=0,
            )
        return self._deepseek

    @property
    def _provider_ollama(self):
        if self._ollama is None:
            self._ollama = _OllamaHTTPProvider(
                model=OLLAMA_MODEL,
                base_url=OLLAMA_BASE_URL,
                timeout=OLLAMA_REQUEST_TIMEOUT,
            )
        return self._ollama

    def _get_active_provider(self) -> tuple[_Provider, str]:
        if (
            self._provider_mode == "auto"
            and not self._deepseek_available
            and not self._ollama_available
            and time.monotonic() < self._providers_unavailable_until
        ):
            raise RuntimeError("所有 LLM 提供商暂时不可用，请稍后重试")
        if self._provider_mode == "deepseek":
            return self._provider_deepseek, "deepseek"
        if self._provider_mode == "ollama":
            return self._provider_ollama, "ollama"
        if (
            self._deepseek_available
            or time.monotonic() >= self._deepseek_retry_at
        ):
            return self._provider_deepseek, "deepseek"
        if self._ollama_available:
            return self._provider_ollama, "ollama"
        self._deepseek_available = True
        return self._provider_deepseek, "deepseek"

    def _on_success(self, provider_name: str) -> None:
        self._providers_unavailable_until = 0.0
        if provider_name == "deepseek" and not self._deepseek_available:
            logger.info("DeepSeek 恢复可用")
            self._deepseek_available = True
            self._fallback_count = 0
            self._deepseek_retry_at = 0.0

    def _on_failure(self, provider_name: str, error: Exception) -> None:
        error_message = stable_error_message(
            error,
            fallback=f"{provider_name} request failed",
        )
        self._last_error = error_message
        logger.warning("%s 调用失败: %s", provider_name, error_message)
        if provider_name == "deepseek":
            self._deepseek_available = False
            self._deepseek_retry_at = (
                time.monotonic() + LLM_PROVIDER_RETRY_AFTER
            )
            self._fallback_count += 1
            if self._provider_mode == "auto" and self._ollama_available:
                logger.info(f"降级到本地 Ollama ({OLLAMA_MODEL})")
        elif provider_name == "ollama":
            self._ollama_available = False
            if self._provider_mode == "auto":
                logger.warning(
                    "Ollama 也失败了，进入 %ss Provider 冷却窗口",
                    LLM_PROVIDER_COOLDOWN,
                )
        if not self._deepseek_available and not self._ollama_available:
            # Stop planner/replan loops from paying two network timeouts on
            # every attempt after both providers have just failed.
            self._providers_unavailable_until = (
                time.monotonic() + LLM_PROVIDER_COOLDOWN
            )

    def _invoke_with_retries(self, provider, name, messages, kwargs):
        """Retry transient connectivity failures before giving up on a
        provider; 4xx/auth errors raise immediately."""
        for attempt in range(LLM_PROVIDER_MAX_RETRIES + 1):
            try:
                return provider.invoke(messages, **kwargs)
            except Exception as e:
                if (
                    attempt >= LLM_PROVIDER_MAX_RETRIES
                    or not _retryable_provider_error(e)
                ):
                    raise
                time.sleep(min(0.5 * (attempt + 1), 2.0))

    async def _acall_with_retries(self, call, provider, name):
        for attempt in range(LLM_PROVIDER_MAX_RETRIES + 1):
            try:
                return await await_interruptibly(
                    call(provider, name),
                    timeout=_request_timeout(provider),
                )
            except RunInterruptionRequested:
                raise
            except Exception as e:
                if (
                    attempt >= LLM_PROVIDER_MAX_RETRIES
                    or not _retryable_provider_error(e)
                ):
                    raise
                await asyncio.sleep(min(0.5 * (attempt + 1), 2.0))

    def invoke(self, messages: list[BaseMessage], **kwargs):
        messages = _inject_time_to_system(messages)
        provider, name = self._get_active_provider()
        try:
            result = self._invoke_with_retries(provider, name, messages, kwargs)
            self._on_success(name)
            return result
        except Exception as e:
            self._on_failure(name, e)
            if self._provider_mode != "auto":
                raise ProviderError(
                    "指定的 LLM Provider 不可用 "
                    f"({name}): {stable_error_message(e, fallback=f'{name} request failed')}", e,
                ) from e
            attempted = {name}
            last_error: Exception = e
            fallback_provider, fallback_name = self._get_active_provider()
            if fallback_name != name:
                attempted.add(fallback_name)
                try:
                    result = self._invoke_with_retries(
                        fallback_provider, fallback_name, messages, kwargs
                    )
                    self._on_success(fallback_name)
                    return result
                except Exception as e2:
                    self._on_failure(fallback_name, e2)
                    last_error = e2
            # Do not retry a provider already attempted in this request. A
            # failed DeepSeek -> failed Ollama sequence must become one stable
            # provider failure, not a third identical DeepSeek call.
            if "deepseek" not in attempted:
                try:
                    result = self._provider_deepseek.invoke(messages, **kwargs)
                    self._deepseek_available = True
                    return result
                except Exception as e3:
                    self._on_failure("deepseek", e3)
                    last_error = e3
            raise ProviderError(
                "所有 LLM 提供商均不可用。最后一次错误: "
                f"{stable_error_message(last_error, fallback='all provider requests failed')}", last_error,
            ) from last_error

    async def _aexecute(
        self,
        call: Callable[[Any, str], Awaitable[Any]],
    ):
        """Run one provider call with the shared fallback/timeout policy."""
        provider, name = self._get_active_provider()
        try:
            result = await self._acall_with_retries(call, provider, name)
            self._on_success(name)
            return result
        except RunInterruptionRequested:
            raise
        except Exception as e:
            self._on_failure(name, e)
            if self._provider_mode != "auto":
                raise ProviderError(
                    "指定的 LLM Provider 不可用 "
                    f"({name}): {stable_error_message(e, fallback=f'{name} request failed')}", e,
                ) from e
            attempted = {name}
            last_error: Exception = e
            fallback_provider, fallback_name = self._get_active_provider()
            if fallback_name != name:
                attempted.add(fallback_name)
                try:
                    result = await self._acall_with_retries(
                        call, fallback_provider, fallback_name
                    )
                    self._on_success(fallback_name)
                    return result
                except RunInterruptionRequested:
                    raise
                except Exception as e2:
                    self._on_failure(fallback_name, e2)
                    last_error = e2
            if "deepseek" not in attempted:
                try:
                    result = await await_interruptibly(
                        call(self._provider_deepseek, "deepseek"),
                        timeout=_request_timeout(self._provider_deepseek),
                    )
                    self._deepseek_available = True
                    return result
                except RunInterruptionRequested:
                    raise
                except Exception as e3:
                    self._on_failure("deepseek", e3)
                    last_error = e3
            raise ProviderError(
                "所有 LLM 提供商均不可用。最后一次错误: "
                f"{stable_error_message(last_error, fallback='all provider requests failed')}", last_error,
            ) from last_error

    async def ainvoke(self, messages: list[BaseMessage], **kwargs):
        messages = _inject_time_to_system(messages)
        return await self._aexecute(
            lambda provider, _name: provider.ainvoke(messages, **kwargs)
        )

    async def achat(
        self,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
        **kwargs: Any,
    ) -> ChatTurn:
        """Native tool-calling chat; transcript is canonical dict messages."""
        rendered = _inject_time_to_transcript(messages)
        return await self._aexecute(
            lambda provider, name: (
                _deepseek_achat(provider, rendered, tools, kwargs)
                if name == "deepseek"
                else provider.achat(rendered, tools, **kwargs)
            )
        )

    @property
    def status(self) -> dict:
        return {
            "provider_mode": self._provider_mode,
            "deepseek": {
                "model": DEEPSEEK_MODEL,
                "available": self._deepseek_available,
            },
            "ollama": {
                "model": OLLAMA_MODEL,
                "available": self._ollama_available,
            },
            "fallback_count": self._fallback_count,
            "last_error": self._last_error,
        }

    def bind_tools(self, tools: list):
        provider, name = self._get_active_provider()
        return provider.bind_tools(tools)


llm = LLMRouter()
