"""DuckDuckGo search provider — wraps the internal-only DDG MCP spec."""
from __future__ import annotations

from agent.search.providers import SearchOutcome


class DuckDuckGoSearchProvider:
    id = "duckduckgo"

    def available(self) -> bool:
        from agent.mcp.duckduckgo import unavailable_reason

        return not unavailable_reason()

    async def search(self, query: str, *, max_results: int) -> SearchOutcome:
        from agent.mcp.duckduckgo import call_duckduckgo_tool
        from tools.web import parse_search_results

        text = await call_duckduckgo_tool(
            "search", {"query": query, "maxResults": max_results}
        )
        sources = [item.model_dump() for item in parse_search_results(text)]
        return SearchOutcome(provider=self.id, sources=sources, content=text)
