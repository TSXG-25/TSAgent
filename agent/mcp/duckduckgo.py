"""Client for the local DuckDuckGo MCP server."""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import timedelta

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import TextContent


SERVER_COMMAND = "duckduckgo-mcp-server"


def _missing_server_reason() -> str:
    return "duckduckgo-mcp-server is not installed; install the project's requirements"


def unavailable_reason() -> str:
    """Return why the local DuckDuckGo MCP server cannot be launched."""
    return _missing_server_reason() if shutil.which(SERVER_COMMAND) is None else ""


@asynccontextmanager
async def duckduckgo_mcp_client() -> AsyncIterator[
    Callable[[str, dict[str, object]], Awaitable[str]]
]:
    """Open one local server session and expose its discovered tools."""
    command = shutil.which(SERVER_COMMAND)
    if command is None:
        raise RuntimeError(
            f"RESEARCH_TOOL_UNAVAILABLE: {_missing_server_reason()}"
        )

    server = StdioServerParameters(
        command=command,
        env={
            # Keep real URLs in search output because the MCP process is scoped to
            # this tool call, so its in-memory ref:// tokens would not survive.
            "DDG_REF_URL_THRESHOLD": "0",
            "DDG_SEARCH_BACKEND": "auto",
            # Forward the opt-in SSRF bypass: under fake-IP DNS (Clash/V2Ray TUN
            # mode) every domain resolves to 198.18.x.x and the server's private-
            # address check rejects all fetches.
            "DDG_ALLOW_PRIVATE_URLS": os.environ.get(
                "DDG_ALLOW_PRIVATE_URLS", "0"
            ),
        },
    )
    async with stdio_client(server) as (read_stream, write_stream):
        async with ClientSession(
            read_stream,
            write_stream,
            read_timeout_seconds=timedelta(seconds=90),
        ) as session:
            await session.initialize()
            available = await session.list_tools()
            available_names = {tool.name for tool in available.tools}

            async def call_tool(name: str, arguments: dict[str, object]) -> str:
                if name not in available_names:
                    raise RuntimeError(
                        f"RESEARCH_TOOL_UNAVAILABLE: DuckDuckGo MCP tool is missing: {name}"
                    )

                result = await session.call_tool(name, arguments)
                if result.isError:
                    detail = "\n".join(
                        block.text for block in result.content
                        if isinstance(block, TextContent)
                    )
                    raise RuntimeError(
                        "RESEARCH_TOOL_UNAVAILABLE: DuckDuckGo MCP tool failed"
                        + (f": {detail}" if detail else "")
                    )

                text = "\n".join(
                    block.text for block in result.content
                    if isinstance(block, TextContent)
                )
                if text:
                    return text
                if result.structuredContent is not None:
                    return json.dumps(result.structuredContent, ensure_ascii=False)
                return ""

            yield call_tool


async def call_duckduckgo_tool(name: str, arguments: dict[str, object]) -> str:
    """Start the local server, call one tool, and close the process."""

    async with duckduckgo_mcp_client() as call_tool:
        return await call_tool(name, arguments)
