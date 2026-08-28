"""LLM 抽象层 — 主用 DeepSeek，失败自动降级到本地 Ollama。

提供与之前一致的 `llm.ainvoke(messages)` 接口，所有使用者无需修改。
同时自动在所有请求中注入当前时间信息。
"""
import os
import logging
import time
from types import SimpleNamespace
from typing import Any, Final, Protocol
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

from langchain_core.messages import SystemMessage, BaseMessage
from agent.interruption import RunInterruptionRequested, await_interruptibly

logger = logging.getLogger(__name__)

# --- Configuration ---
DEEPSEEK_API_KEY = os.getenv("OPENAI_API_KEY", "")
DEEPSEEK_MODEL = os.getenv("MODEL_NAME", "deepseek-v4-flash")
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"

# Ollama (local fallback)
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:8b")
LLM_REQUEST_TIMEOUT: Final[float] = float(os.getenv("TSAGENT_LLM_TIMEOUT", "45"))
LLM_PROVIDER_COOLDOWN: Final[float] = float(
    os.getenv("TSAGENT_LLM_PROVIDER_COOLDOWN", "5")
)
_VALID_PROVIDER_MODES = {"auto", "deepseek", "ollama"}


class _Provider(Protocol):
    def invoke(self, messages: list[BaseMessage], **kwargs: object) -> Any: ...

    async def ainvoke(self, messages: list[BaseMessage], **kwargs: object) -> Any: ...

    def with_structured_output(self, *args: object, **kwargs: object) -> Any: ...

    def bind_tools(self, tools: list[object]) -> Any: ...


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

    def _payload(
        self,
        messages: list[BaseMessage],
        kwargs: dict[str, object],
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "model": self.model,
            "messages": self._messages(messages),
            "temperature": kwargs.get("temperature", 0),
        }
        for key in ("max_tokens", "top_p", "stop"):
            if key in kwargs:
                payload[key] = kwargs[key]
        return payload

    @staticmethod
    def _response(response: Any) -> SimpleNamespace:
        data = response.json()
        choices = data.get("choices", []) if isinstance(data, dict) else []
        if not choices or not isinstance(choices[0], dict):
            raise RuntimeError("Ollama returned no chat completion choices")
        message = choices[0].get("message", {})
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
                f"{self.base_url}/chat/completions",
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
                f"{self.base_url}/chat/completions",
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

    def bind_tools(self, _tools: list[object]) -> object:
        raise RuntimeError("Ollama tool binding is not supported by this adapter")


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
        self._structured_output_available = True  # 缓存 structured output 可用性
        self._structured_output_tested = False

    @property
    def supports_structured_output(self) -> bool:
        """检测当前 provider 是否支持 structured output。
        
        第一次调用会测试，失败后永久关闭。
        """
        if self._structured_output_tested:
            return self._structured_output_available
        self._structured_output_tested = True
        provider, name = self._get_active_provider()
        if name == "ollama":
            self._structured_output_available = False
            return False
        # 对于 DeepSeek，with_structured_output 可能返回 400 错误
        # 我们在 Planner 中会 catch 这个错误并标记为不可用
        return True

    def disable_structured_output(self):
        """永久关闭 structured output（例如第一次调用失败后）。"""
        self._structured_output_available = False
        self._structured_output_tested = True

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
                timeout=LLM_REQUEST_TIMEOUT,
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
        if self._deepseek_available:
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

    def _on_failure(self, provider_name: str, error: Exception) -> None:
        self._last_error = str(error)
        logger.warning(f"{provider_name} 调用失败: {error}")
        if provider_name == "deepseek":
            self._deepseek_available = False
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

    def invoke(self, messages: list[BaseMessage], **kwargs):
        messages = _inject_time_to_system(messages)
        provider, name = self._get_active_provider()
        try:
            result = provider.invoke(messages, **kwargs)
            self._on_success(name)
            return result
        except Exception as e:
            self._on_failure(name, e)
            if self._provider_mode != "auto":
                raise RuntimeError(
                    f"指定的 LLM Provider 不可用 ({name}): {e}"
                ) from e
            attempted = {name}
            last_error: Exception = e
            fallback_provider, fallback_name = self._get_active_provider()
            if fallback_name != name:
                attempted.add(fallback_name)
                try:
                    result = fallback_provider.invoke(messages, **kwargs)
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
            raise RuntimeError(
                f"所有 LLM 提供商均不可用。最后一次错误: {last_error}"
            ) from last_error

    async def ainvoke(self, messages: list[BaseMessage], **kwargs):
        messages = _inject_time_to_system(messages)
        provider, name = self._get_active_provider()
        try:
            result = await await_interruptibly(
                provider.ainvoke(messages, **kwargs),
                timeout=LLM_REQUEST_TIMEOUT,
            )
            self._on_success(name)
            return result
        except RunInterruptionRequested:
            raise
        except Exception as e:
            self._on_failure(name, e)
            if self._provider_mode != "auto":
                raise RuntimeError(
                    f"指定的 LLM Provider 不可用 ({name}): {e}"
                ) from e
            attempted = {name}
            last_error: Exception = e
            fallback_provider, fallback_name = self._get_active_provider()
            if fallback_name != name:
                attempted.add(fallback_name)
                try:
                    result = await await_interruptibly(
                        fallback_provider.ainvoke(messages, **kwargs),
                        timeout=LLM_REQUEST_TIMEOUT,
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
                        self._provider_deepseek.ainvoke(messages, **kwargs),
                        timeout=LLM_REQUEST_TIMEOUT,
                    )
                    self._deepseek_available = True
                    return result
                except RunInterruptionRequested:
                    raise
                except Exception as e3:
                    self._on_failure("deepseek", e3)
                    last_error = e3
            raise RuntimeError(
                f"所有 LLM 提供商均不可用。最后一次错误: {last_error}"
            ) from last_error

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
