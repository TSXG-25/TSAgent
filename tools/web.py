"""Web search and page extraction through the DuckDuckGo MCP server."""

import asyncio
import re
from datetime import date, timedelta
from typing import Annotated, Any, Iterable, Literal, Mapping

from pydantic import BaseModel, Field

from agent.registry.tool_registry import ToolAvailability, registry


class SearchResult(BaseModel):
    """Canonical structured web-search result item.

    Fields the provider cannot supply stay empty; nothing is fabricated.
    """

    title: str = ""
    url: str = ""
    snippet: str = ""
    source: str = ""
    published: str = ""


SEARCH_RESULT_SCHEMA: dict = {
    "type": "array",
    "items": SearchResult.model_json_schema(),
}


def parse_search_results(text: str, *, source: str = "duckduckgo") -> list[SearchResult]:
    """Parse the DuckDuckGo MCP text output into canonical result items.

    The MCP server renders one block per result: a numbered title line
    followed by indented ``URL:``/``Summary:`` fields.  Bare ``Title:`` /
    ``URL:`` key lines are accepted as well.  Prose such as the server's
    "no results" message yields an empty list.
    """
    results: list[SearchResult] = []
    current: dict[str, str] | None = None

    def flush() -> None:
        nonlocal current
        if current is not None and (current.get("title") or current.get("url")):
            results.append(SearchResult(source=source, **current))
        current = None

    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line:
            flush()
            continue
        numbered = re.match(r"^\d+[.)]\s+(.*\S)\s*$", line)
        if numbered:
            flush()
            current = {"title": numbered.group(1).strip()}
            continue
        field = re.match(
            r"^(title|url|summary|snippet|description|published|date)\s*:\s*(.*)$",
            line,
            flags=re.IGNORECASE,
        )
        if field:
            key, value = field.group(1).lower(), field.group(2).strip()
            if key == "title":
                flush()
                current = {"title": value}
                continue
            if current is None:
                current = {}
            if key == "url":
                current["url"] = value
            elif key in {"summary", "snippet", "description"}:
                current["snippet"] = value
            else:
                current["published"] = value
            continue
        # Unstructured lines (e.g. "Found N search results:") are skipped.
    flush()
    return results


def render_search_results(
    results: Iterable[SearchResult | Mapping[str, Any]],
    *,
    notice: str = "",
) -> str:
    """Canonical text projection of structured search results.

    This is the single place where the structured contract becomes prose
    for consumers that only need text (LLM prompts, action summaries).
    """
    lines: list[str] = [notice.rstrip("\n")] if notice else []
    for index, item in enumerate(results, 1):
        fields = item.model_dump() if isinstance(item, SearchResult) else dict(item)
        title = str(fields.get("title", "")).strip()
        url = str(fields.get("url", "")).strip()
        snippet = str(fields.get("snippet", "")).strip()
        source = str(fields.get("source", "")).strip()
        published = str(fields.get("published", "")).strip()
        entry = f"{index}. {title or url}"
        if url:
            entry += f"\n   URL: {url}"
        if snippet:
            entry += f"\n   Summary: {snippet}"
        meta = " · ".join(
            part
            for part in (
                f"Source: {source}" if source else "",
                f"Published: {published}" if published else "",
            )
            if part
        )
        if meta:
            entry += f"\n   {meta}"
        lines.append(entry)
    return "\n".join(lines)


def _duckduckgo_availability() -> ToolAvailability:
    from agent.mcp.duckduckgo import unavailable_reason

    reason = unavailable_reason()
    if reason:
        return ToolAvailability(
            available=False,
            reason_code="RESEARCH_TOOL_UNAVAILABLE",
            reason=reason,
        )
    return ToolAvailability(available=True)


TIME_FILTER_NOTICE = (
    "说明：DuckDuckGo MCP 不提供精确时间过滤；时间范围仅作为搜索词提示，"
    "结果可能包含该范围之外的页面。\n\n"
)


def _time_aware_query(query: str, timeliness: str) -> str:
    today = date.today()
    if timeliness == "today":
        return f"{today:%Y年%m月%d日} {query}"
    if timeliness == "yesterday":
        yesterday = today - timedelta(days=1)
        return f"{yesterday:%Y年%m月%d日} {query}"
    if timeliness == "week":
        week_ago = today - timedelta(days=7)
        return f"{query} 最近一周 自 {week_ago.isoformat()}"
    if timeliness == "month":
        return f"{today:%Y年%m月} {query}"
    if timeliness == "any":
        return query
    raise ValueError(f"unsupported search timeliness: {timeliness}")


async def _call_duckduckgo_tool(name: str, arguments: dict[str, object]) -> str:
    from agent.mcp.duckduckgo import call_duckduckgo_tool

    return await call_duckduckgo_tool(name, arguments)


async def web_search(
    query: str = "",
    q: str = "",
    url: str = "",
    keyword: str = "",
    search: str = "",
    max_results: Annotated[int, Field(ge=5, le=20)] = 5,
    timeliness: Literal["today", "yesterday", "week", "month", "any"] = "any",
) -> list[SearchResult]:
    """Search DuckDuckGo through MCP and return structured results."""

    query = query or q or keyword or search or url
    if not query:
        raise ValueError("web_search 缺少 query 参数")

    result = await _call_duckduckgo_tool(
        "search",
        {"query": _time_aware_query(query, timeliness), "max_results": max_results},
    )
    return parse_search_results(result)


async def web_news_search(
    query: str,
    max_results: Annotated[int, Field(ge=5, le=20)] = 5,
    days: Annotated[int, Field(gt=0)] = 7,
) -> list[SearchResult]:
    """Search for news; explicitly disclose that DuckDuckGo MCP cannot date-filter."""

    cutoff = date.today() - timedelta(days=days)
    result = await _call_duckduckgo_tool(
        "search",
        {
            "query": f"{query} news since {cutoff.isoformat()}",
            "max_results": max_results,
        },
    )
    return parse_search_results(result)


async def web_deep_search(
    query: str,
    fetch_top_n: Annotated[int, Field(ge=1, le=20)] = 2,
    timeliness: Literal["today", "yesterday", "week", "month", "any"] = "any",
) -> str:
    """Search with MCP, then fetch the top result pages through the same server."""

    from agent.mcp.duckduckgo import duckduckgo_mcp_client

    async with duckduckgo_mcp_client() as call_tool:
        search_result = await call_tool(
            "search",
            {
                "query": _time_aware_query(query, timeliness),
                "max_results": min(20, max(5, fetch_top_n)),
            },
        )
        urls = list(dict.fromkeys(
            item.url for item in parse_search_results(search_result) if item.url
        ))[:fetch_top_n]
        pages = await asyncio.gather(*(
            call_tool(
                "fetch_content",
                {"url": url, "max_length": 8000, "backend": "auto"},
            )
            for url in urls
        ))

    if not urls:
        if timeliness != "any":
            return TIME_FILTER_NOTICE + search_result
        return search_result
    sections = [search_result, "\n\n## 页面内容\n"]
    sections.extend(f"### {url}\n{page}" for url, page in zip(urls, pages))
    result = "\n\n".join(sections)
    if timeliness != "any":
        return TIME_FILTER_NOTICE + result
    return result


async def web_fetch(url: str) -> str:
    """Fetch a known public webpage through the DuckDuckGo MCP server."""

    if not url:
        return "错误：web_fetch 缺少 url 参数"
    return await _call_duckduckgo_tool(
        "fetch_content",
        {"url": url, "max_length": 8000, "backend": "auto"},
    )


registry.register(
    web_search,
    category="web",
    tags=["search", "web"],
    availability=_duckduckgo_availability,
)
registry.register(
    web_fetch,
    category="web",
    tags=["search", "fetch", "web"],
    availability=_duckduckgo_availability,
)
registry.register(
    web_deep_search,
    category="web",
    tags=["search", "deep", "fetch", "web"],
    availability=_duckduckgo_availability,
)
registry.register(
    web_news_search,
    category="web",
    tags=["search", "news", "time"],
    availability=_duckduckgo_availability,
)
