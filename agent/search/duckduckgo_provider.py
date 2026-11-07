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

        text = await call_duckduckgo_tool(
            "search", {"query": query, "maxResults": max_results}
        )
        return SearchOutcome(provider=self.id, content=text)
