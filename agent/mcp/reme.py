"""ReMe memory service client — streamable-HTTP MCP.

TSAgent treats ReMe as an optional local sidecar (the same posture as the
DuckDuckGo MCP server): ``TSAGENT_REME_URL`` points at the service's MCP
endpoint (default ``http://127.0.0.1:2333/mcp``) and every call opens one
short-lived session.  The service is the memory owner: it is started
lazily on first use (detached, so it keeps running between runs) and
failures surface as plain tool errors / logged background-task failures
— never blocking the core response path.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
from datetime import timedelta
from urllib.parse import urlparse

from mcp import ClientSession
from mcp.types import TextContent

REME_MCP_URL = os.environ.get("TSAGENT_REME_URL", "http://127.0.0.1:2333/mcp")
_SEARCH_TIMEOUT = 30
_AUTO_MEMORY_TIMEOUT = 180
_SPAWN_READY_TIMEOUT = 45.0

_service_lock = asyncio.Lock()
_spawn_failed = False


def _endpoint_unreachable() -> bool:
    parsed = urlparse(REME_MCP_URL)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=1):
            return False
    except OSError:
        return True


async def _ensure_service() -> None:
    """Lazily start `reme start` once; the detached service outlives us."""
    global _spawn_failed
    if _spawn_failed:
        raise RuntimeError("REME_UNAVAILABLE: service spawn previously failed")
    if not _endpoint_unreachable():
        return
    async with _service_lock:
        if not _endpoint_unreachable():
            return
        command = shutil.which("reme")
        if command is None:
            _spawn_failed = True
            raise RuntimeError(
                "REME_UNAVAILABLE: `reme` not on PATH "
                "(install with: pip install 'reme-ai[core]')"
            )
        subprocess.Popen(
            [command, "start"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        waited = 0.0
        while _endpoint_unreachable() and waited < _SPAWN_READY_TIMEOUT:
            await asyncio.sleep(0.5)
            waited += 0.5
        if _endpoint_unreachable():
            _spawn_failed = True
            raise RuntimeError(
                "REME_UNAVAILABLE: `reme start` did not become ready "
                f"within {_SPAWN_READY_TIMEOUT:.0f}s"
            )


async def call_reme_tool(
    name: str,
    arguments: dict[str, object],
    *,
    timeout: int = _SEARCH_TIMEOUT,
) -> str:
    """Open one MCP session against the ReMe service and call one tool."""
    await _ensure_service()
    from mcp.client.streamable_http import streamablehttp_client

    async with streamablehttp_client(
        REME_MCP_URL,
        timeout=timedelta(seconds=timeout),
    ) as (read_stream, write_stream, _):
        async with ClientSession(
            read_stream,
            write_stream,
            read_timeout_seconds=timedelta(seconds=timeout),
        ) as session:
            await session.initialize()
            result = await session.call_tool(name, arguments)
            if result.isError:
                detail = "\n".join(
                    block.text for block in result.content
                    if isinstance(block, TextContent)
                )
                raise RuntimeError(
                    "REME_TOOL_FAILED"
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


async def reme_search(query: str, *, limit: int = 8) -> str:
    """Search the long-term memory workspace (BM25/wikilink/graph sidecar)."""
    return await call_reme_tool("search", {"query": query, "limit": limit})


async def reme_auto_memory(
    messages: list[dict[str, str]],
    *,
    session_id: str = "",
    request_id: str = "",
) -> str:
    """Submit completed turns for long-term distillation by the ReMe agent.

    ``request_id`` makes the batch idempotent so a retried submission does
    not duplicate the daily-note update.
    """
    arguments: dict[str, object] = {"messages": messages}
    if session_id:
        arguments["session_id"] = session_id
    if request_id:
        arguments["request_id"] = request_id
    return await call_reme_tool(
        "auto_memory", arguments, timeout=_AUTO_MEMORY_TIMEOUT
    )
