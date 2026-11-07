"""Search provider seam（参照 dsh ctx.web 的最小实现）。

Provider 注册的是"搜索能力"而不是工具；模型面 `web_search` 只经
`run_search()` 选择 provider，换 provider 不动模型 schema。

- `available()` 必须是廉价本地检查（凭证/二进制存在性），禁止发网络请求；
- `run_search` 按配置顺序 failover，全部失败抛 RuntimeError；
- 结果归一化为 SearchOutcome(sources + content)。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Protocol

SEARCH_PROVIDERS_ENV = "TSAGENT_SEARCH_PROVIDERS"
DEFAULT_ORDER = ("duckduckgo", "deepseek")


@dataclass
class SearchOutcome:
    provider: str
    sources: list[dict] = field(default_factory=list)
    content: str = ""


class SearchProvider(Protocol):
    id: str

    def available(self) -> bool: ...

    async def search(self, query: str, *, max_results: int) -> SearchOutcome: ...


def provider_order() -> tuple[str, ...]:
    raw = os.getenv(SEARCH_PROVIDERS_ENV, "")
    if raw.strip():
        return tuple(x.strip() for x in raw.split(",") if x.strip())
    return DEFAULT_ORDER


def _registry() -> dict[str, SearchProvider]:
    from agent.search.duckduckgo_provider import DuckDuckGoSearchProvider
    from agent.search.deepseek_provider import DeepSeekSearchProvider

    return {
        "duckduckgo": DuckDuckGoSearchProvider(),
        "deepseek": DeepSeekSearchProvider(),
    }


async def run_search(query: str, *, max_results: int = 8) -> SearchOutcome:
    registry = _registry()
    reasons: list[str] = []
    for pid in provider_order():
        provider = registry.get(pid)
        if provider is None:
            reasons.append(f"{pid}: unknown provider")
            continue
        if not provider.available():
            reasons.append(f"{pid}: unavailable")
            continue
        try:
            return await provider.search(query, max_results=max_results)
        except Exception as e:
            reasons.append(f"{pid}: {e}")
    raise RuntimeError(
        "RESEARCH_TOOL_UNAVAILABLE: no search provider succeeded — "
        + "; ".join(reasons)
    )
