"""LLM 抽象层 — 主用 DeepSeek，失败自动降级到本地 Ollama。

提供与之前一致的 `llm.ainvoke(messages)` 接口，所有使用者无需修改。
同时自动在所有请求中注入当前时间信息。
"""
import os
import logging
from typing import Final
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

from langchain_openai import ChatOpenAI
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
    def __init__(self):
        self._deepseek = None
        self._ollama = None
        self._deepseek_available = True
        self._ollama_available = True
        self._fallback_count = 0
        self._last_error = ""
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
    def _provider_deepseek(self) -> ChatOpenAI:
        if self._deepseek is None:
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
    def _provider_ollama(self) -> ChatOpenAI:
        if self._ollama is None:
            self._ollama = ChatOpenAI(
                model=OLLAMA_MODEL,
                openai_api_key="ollama",
                openai_api_base=OLLAMA_BASE_URL,
                temperature=0,
                timeout=LLM_REQUEST_TIMEOUT,
                max_retries=0,
            )
        return self._ollama

    def _get_active_provider(self) -> tuple[ChatOpenAI, str]:
        if self._deepseek_available:
            return self._provider_deepseek, "deepseek"
        if self._ollama_available:
            return self._provider_ollama, "ollama"
        self._deepseek_available = True
        return self._provider_deepseek, "deepseek"

    def _on_success(self, provider_name: str) -> None:
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
            if self._ollama_available:
                logger.info(f"降级到本地 Ollama ({OLLAMA_MODEL})")
        elif provider_name == "ollama":
            self._ollama_available = False
            logger.warning("Ollama 也失败了，尝试重试 DeepSeek")
            self._deepseek_available = True

    def invoke(self, messages: list[BaseMessage], **kwargs):
        messages = _inject_time_to_system(messages)
        provider, name = self._get_active_provider()
        try:
            result = provider.invoke(messages, **kwargs)
            self._on_success(name)
            return result
        except Exception as e:
            self._on_failure(name, e)
            fallback_provider, fallback_name = self._get_active_provider()
            if fallback_name != name:
                try:
                    result = fallback_provider.invoke(messages, **kwargs)
                    self._on_success(fallback_name)
                    return result
                except Exception as e2:
                    self._on_failure(fallback_name, e2)
            try:
                result = self._provider_deepseek.invoke(messages, **kwargs)
                self._deepseek_available = True
                return result
            except Exception as e3:
                raise RuntimeError(
                    f"所有 LLM 提供商均不可用。最后一次错误: {e3}"
                ) from e3

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
            fallback_provider, fallback_name = self._get_active_provider()
            if fallback_name != name:
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
                raise RuntimeError(
                    f"所有 LLM 提供商均不可用。最后一次错误: {e3}"
                ) from e3

    @property
    def status(self) -> dict:
        return {
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
