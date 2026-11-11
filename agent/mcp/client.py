"""Shared MCP server client: specs, lazy service start, one-call sessions.

Two transports: local stdio processes and streamable-HTTP endpoints.  Each
call opens a short-lived session (the same posture the per-server adapters
had); availability and failures surface through per-spec error codes so
callers keep their existing classification contracts.

A server is a ``MCPServerSpec`` row in ``agent.mcp.servers`` — adding one
does not require new adapter code.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
import socket
import subprocess
from collections import defaultdict
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from urllib.parse import urlparse

import asyncio

from mcp import ClientSession, StdioServerParameters
from mcp.types import TextContent

ToolCaller = Callable[[str, dict[str, object]], Awaitable[str]]


@dataclass(frozen=True)
class MCPServerSpec:
    """One MCP server's connection and error contract."""

    name: str
    transport: str  # "stdio" | "http"
    title: str = ""
    command: str = ""  # stdio executable name resolved via PATH
    args: tuple[str, ...] = ()  # stdio argv after the executable
    url: str = ""  # streamable-HTTP endpoint
    env: Mapping[str, str] | Callable[[], dict[str, str]] = field(
        default_factory=dict
    )
    # Lazy detached service start for http specs whose endpoint is a local
    # long-running sidecar (started once, outlives the caller).
    spawn_command: tuple[str, ...] = ()
    spawn_ready_timeout: float = 45.0
    spawn_hint: str = ""
    missing_hint: str = ""
    # Error taxonomy: unavailable_code covers spawn/missing-tool/missing-
    # binary; tool_error_code covers isError results (defaults to the
    # unavailable code, matching single-code adapters).
    unavailable_code: str = "MCP_UNAVAILABLE"
    tool_error_code: str = ""
    session_timeout: int = 90
    # Explicit allowlist of server-side tool names exposed to the model's
    # loop catalog.  Empty means the server is internal-only: exposure is
    # a deliberate contract decision, not an accident of connecting.
    expose_tools: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        return self.title or self.name


_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


def _endpoint_reachable(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def _server_env(spec: MCPServerSpec) -> dict[str, str]:
    env = spec.env
    return dict(env() if callable(env) else env)


def unavailable_reason(spec: MCPServerSpec) -> str:
    """Cheap availability check; "" means the server should be usable."""
    if spec.transport == "stdio":
        if shutil.which(spec.command) is None:
            return spec.missing_hint or f"{spec.command} is not installed"
        return ""
    if _endpoint_reachable(spec.url):
        return ""
    if spec.spawn_command and shutil.which(spec.spawn_command[0]) is None:
        return spec.spawn_hint or (
            f"{spec.spawn_command[0]} is not installed and the endpoint "
            f"is unreachable: {spec.url}"
        )
    # Endpoint down but spawnable — let the lazy-start path decide.
    return ""


async def _ensure_server(spec: MCPServerSpec) -> None:
    """Lazily start a local HTTP sidecar (detached, outlives caller)."""
    if spec.transport != "http" or not spec.spawn_command:
        return
    if _endpoint_reachable(spec.url):
        return
    async with _locks[spec.name]:
        if _endpoint_reachable(spec.url):
            return
        binary = shutil.which(spec.spawn_command[0])
        if binary is None:
            raise RuntimeError(
                spec.spawn_hint
                or f"{spec.unavailable_code}: `{spec.spawn_command[0]}` not on PATH"
            )
        subprocess.Popen(
            [binary, *spec.spawn_command[1:]],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        waited = 0.0
        while not _endpoint_reachable(spec.url):
            if waited >= spec.spawn_ready_timeout:
                raise RuntimeError(
                    f"{spec.unavailable_code}: `{' '.join(spec.spawn_command)}` "
                    f"did not become ready within {spec.spawn_ready_timeout:.0f}s"
                )
            await asyncio.sleep(0.5)
            waited += 0.5


@asynccontextmanager
async def _open_session(
    spec: MCPServerSpec, *, timeout: int | None = None
) -> AsyncIterator[Any]:
    """Connect, initialize and yield the raw ClientSession."""
    effective_timeout = timeout or spec.session_timeout
    async with AsyncExitStack() as stack:
        if spec.transport == "stdio":
            command = shutil.which(spec.command)
            if command is None:
                raise RuntimeError(
                    f"{spec.unavailable_code}: "
                    f"{spec.missing_hint or spec.command + ' is not installed'}"
                )
            # 子进程 stderr 落盘而不直通终端：server 退出噪音
            # （如 excel-mcp 的 closed-file traceback）不污染会话，仍留档可查。
            log_path = Path(tempfile.gettempdir()) / f"tsagent-mcp-{spec.name}.log"
            errlog = stack.enter_context(log_path.open("a"))
            read_stream, write_stream = await stack.enter_async_context(
                _stdio_transport(command, spec, errlog)
            )
        elif spec.transport == "http":
            await _ensure_server(spec)
            from mcp.client.streamable_http import streamable_http_client

            read_stream, write_stream, _ = await stack.enter_async_context(
                streamable_http_client(spec.url)
            )
        else:
            raise RuntimeError(
                f"{spec.unavailable_code}: unknown transport {spec.transport!r}"
            )
        session = await stack.enter_async_context(
            ClientSession(
                read_stream,
                write_stream,
                read_timeout_seconds=timedelta(seconds=effective_timeout),
            )
        )
        await session.initialize()
        yield session


@asynccontextmanager
async def mcp_session(
    spec: MCPServerSpec, *, timeout: int | None = None
) -> AsyncIterator[ToolCaller]:
    """Open one server session and yield a validated tool caller."""
    async with _open_session(spec, timeout=timeout) as session:
        available = {tool.name for tool in (await session.list_tools()).tools}

        async def call_tool(name: str, arguments: dict[str, object]) -> str:
            if name not in available:
                raise RuntimeError(
                    f"{spec.unavailable_code}: {spec.label} MCP tool "
                    f"is missing: {name}"
                )
            result = await session.call_tool(name, arguments)
            if result.isError:
                detail = "\n".join(
                    block.text
                    for block in result.content
                    if isinstance(block, TextContent)
                )
                raise RuntimeError(
                    f"{spec.tool_error_code or spec.unavailable_code}: "
                    f"{spec.label} MCP tool failed"
                    + (f": {detail}" if detail else "")
                )
            text = "\n".join(
                block.text
                for block in result.content
                if isinstance(block, TextContent)
            )
            if text:
                return text
            if result.structuredContent is not None:
                return json.dumps(result.structuredContent, ensure_ascii=False)
            return ""

        yield call_tool


async def list_tools(
    spec: MCPServerSpec, *, timeout: int | None = None
) -> list[Any]:
    """Tool discovery: raises whenever the server cannot answer —
    unavailable, uninstalled, or transport/protocol failure.  Only a real
    MCP response (empty or not) is a cacheable result."""
    reason = unavailable_reason(spec)
    if reason:
        raise RuntimeError(reason)
    async with _open_session(spec, timeout=timeout) as session:
        return list((await session.list_tools()).tools)


def _stdio_transport(command: str, spec: MCPServerSpec, errlog):
    from mcp.client.stdio import stdio_client

    return stdio_client(
        StdioServerParameters(
            command=command, args=list(spec.args), env=_server_env(spec)
        ),
        errlog=errlog,
    )


async def call_mcp_tool(
    spec: MCPServerSpec,
    name: str,
    arguments: dict[str, object],
    *,
    timeout: int | None = None,
) -> str:
    """Open one session, call one tool, and close."""
    async with mcp_session(spec, timeout=timeout) as call_tool:
        return await call_tool(name, arguments)
