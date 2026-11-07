"""DeepSeek 原生搜索 provider — Anthropic 兼容 Messages 端点 + web_search_20250305。

DeepSeek Chat Completions 不带搜索；服务端搜索只在
`https://api.deepseek.com/anthropic` 的 Messages 协议下，经
`web_search_20250305` server-side tool 触发。available() 仅查凭证，
不发网络请求。
"""
from __future__ import annotations

import httpx

from agent.llm import DEEPSEEK_API_KEY, DEEPSEEK_MODEL
from agent.search.providers import SearchOutcome

ANTHROPIC_MESSAGES_URL = "https://api.deepseek.com/anthropic/v1/messages"
_WEB_SEARCH_TOOL = {
    "type": "web_search_20250305",
    "name": "web_search",
    "max_uses": 2,
}
_TIMEOUT = 60.0


class DeepSeekSearchProvider:
    id = "deepseek"

    def available(self) -> bool:
        return bool(DEEPSEEK_API_KEY)

    async def search(self, query: str, *, max_results: int) -> SearchOutcome:
        payload = {
            "model": DEEPSEEK_MODEL,
            "max_tokens": 2048,
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": query}]}
            ],
            "tools": [_WEB_SEARCH_TOOL],
        }
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                ANTHROPIC_MESSAGES_URL,
                headers={
                    "x-api-key": DEEPSEEK_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        sources: list[dict] = []
        texts: list[str] = []
        for block in data.get("content") or []:
            btype = block.get("type")
            if btype == "text":
                texts.append(block.get("text", ""))
            elif btype == "web_search_tool_result":
                for item in block.get("content") or []:
                    if item.get("type") == "web_search_result":
                        sources.append(
                            {
                                "title": item.get("title", ""),
                                "url": item.get("url", ""),
                                "snippet": "",
                                "publishedAt": item.get("page_age") or "",
                            }
                        )
        return SearchOutcome(
            provider=self.id,
            sources=sources[:max_results],
            content="\n".join(t for t in texts if t),
        )
