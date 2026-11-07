"""Client for the local DuckDuckGo MCP server (thin wrapper on the shared
client — see ``agent.mcp.client`` / ``agent.mcp.servers``)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from .client import ToolCaller, call_mcp_tool, mcp_session, unavailable_reason as _reason
from .servers import DUCKDUCKGO

SERVER_COMMAND = DUCKDUCKGO.command


def unavailable_reason() -> str:
    """Return why the local DuckDuckGo MCP server cannot be launched."""
    return _reason(DUCKDUCKGO)


@asynccontextmanager
async def duckduckgo_mcp_client() -> AsyncIterator[ToolCaller]:
    """Open one local server session and expose its discovered tools."""
    async with mcp_session(DUCKDUCKGO) as call_tool:
        yield call_tool


async def call_duckduckgo_tool(name: str, arguments: dict[str, object]) -> str:
    """Start the local server, call one tool, and close the process."""
    return await call_mcp_tool(DUCKDUCKGO, name, arguments)
