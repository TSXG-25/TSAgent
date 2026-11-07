"""ReMe memory service client — streamable-HTTP MCP.

TSAgent treats ReMe as an optional local sidecar (the same posture as the
DuckDuckGo MCP server): ``TSAGENT_REME_URL`` points at the service's MCP
endpoint (default ``http://127.0.0.1:2333/mcp``) and every call opens one
short-lived session.  The service is the memory owner: it is started
lazily on first use (detached, so it keeps running between runs) and
failures surface as plain tool errors / logged background-task failures
— never blocking the core response path.

Transport, lazy start and error mapping live in ``agent.mcp.client``; the
server row is ``agent.mcp.servers.REME``.
"""
from __future__ import annotations

from .client import call_mcp_tool
from .servers import REME

REME_MCP_URL = REME.url
_SEARCH_TIMEOUT = 30
_AUTO_MEMORY_TIMEOUT = 180


async def call_reme_tool(
    name: str,
    arguments: dict[str, object],
    *,
    timeout: int = _SEARCH_TIMEOUT,
) -> str:
    """Open one MCP session against the ReMe service and call one tool."""
    return await call_mcp_tool(REME, name, arguments, timeout=timeout)


async def reme_search(query: str, *, limit: int = 8) -> str:
    """Search the long-term memory workspace (BM25/wikilink/graph sidecar)."""
    return await call_reme_tool("search", {"query": query, "limit": limit})


async def reme_auto_memory(
    messages: list[dict[str, str]],
    *,
    session_id: str = "",
    request_id: str = "",
    memory_hint: str = "",
) -> str:
    """Submit completed turns for long-term distillation by the ReMe agent.

    ``request_id`` makes the batch idempotent so a retried submission does
    not duplicate the daily-note update.  ``memory_hint`` steers the
    distilling agent toward what is worth keeping.
    """
    arguments: dict[str, object] = {"messages": messages}
    if session_id:
        arguments["session_id"] = session_id
    if request_id:
        arguments["request_id"] = request_id
    if memory_hint:
        arguments["memory_hint"] = memory_hint
    return await call_reme_tool(
        "auto_memory", arguments, timeout=_AUTO_MEMORY_TIMEOUT
    )
