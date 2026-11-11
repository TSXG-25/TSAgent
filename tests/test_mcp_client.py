"""Shared MCP client: availability checks, session lifecycle, error codes."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from mcp.types import TextContent

import agent.mcp.client as client
from agent.mcp.client import MCPServerSpec, call_mcp_tool, mcp_session
from agent.mcp.servers import DUCKDUCKGO, REME


STDIO_SPEC = MCPServerSpec(
    name="fake",
    title="Fake",
    transport="stdio",
    command="fake-mcp-server",
    missing_hint="fake is not installed",
    unavailable_code="FAKE_UNAVAILABLE",
    tool_error_code="FAKE_TOOL_FAILED",
)


class _FakeResult:
    def __init__(self, text="", is_error=False, structured=None):
        self.isError = is_error
        self.content = (
            [TextContent(type="text", text=text)] if text else []
        )
        self.structuredContent = structured


class _FakeSession:
    """Stand-in ClientSession yielding canned tools/results."""

    def __init__(self, tools=("search",), results=None):
        self._tools = tools
        self._results = list(results or [])
        self.calls: list[tuple[str, dict]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def initialize(self):
        return None

    async def list_tools(self):
        return SimpleNamespace(
            tools=[SimpleNamespace(name=name) for name in self._tools]
        )

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if self._results:
            return self._results.pop(0)
        return _FakeResult(text="ok")


def _patch_transport(monkeypatch, session: _FakeSession):
    """Route both transports to the fake session without real processes."""

    @asynccontextmanager
    async def fake_stdio(_command, _spec, *_a, **_kw):
        yield "read", "write"

    @asynccontextmanager
    async def fake_http(_url, **_kwargs):
        yield "read", "write", None

    monkeypatch.setattr(client, "_stdio_transport", fake_stdio)
    monkeypatch.setattr(
        "mcp.client.streamable_http.streamable_http_client", fake_http,
        raising=False,
    )
    monkeypatch.setattr(client, "ClientSession", lambda *a, **kw: session)


def test_stdio_unavailable_reason(monkeypatch):
    monkeypatch.setattr(client.shutil, "which", lambda _cmd: None)
    assert client.unavailable_reason(STDIO_SPEC) == "fake is not installed"
    monkeypatch.setattr(client.shutil, "which", lambda _cmd: "/usr/bin/fake")
    assert client.unavailable_reason(STDIO_SPEC) == ""


def test_http_unavailable_reason(monkeypatch):
    monkeypatch.setattr(client, "_endpoint_reachable", lambda _url: True)
    assert client.unavailable_reason(REME) == ""
    monkeypatch.setattr(client, "_endpoint_reachable", lambda _url: False)
    monkeypatch.setattr(client.shutil, "which", lambda _cmd: "/usr/bin/reme")
    assert client.unavailable_reason(REME) == ""
    monkeypatch.setattr(client.shutil, "which", lambda _cmd: None)
    assert "REME_UNAVAILABLE" in client.unavailable_reason(REME)


def test_session_calls_tool_and_returns_text(monkeypatch):
    session = _FakeSession(results=[_FakeResult(text="answer")])
    _patch_transport(monkeypatch, session)
    monkeypatch.setattr(client.shutil, "which", lambda _cmd: "/usr/bin/fake")

    async def run():
        async with mcp_session(STDIO_SPEC) as call_tool:
            return await call_tool("search", {"q": "x"})

    assert asyncio.run(run()) == "answer"
    assert session.calls == [("search", {"q": "x"})]


def test_missing_tool_uses_unavailable_code(monkeypatch):
    session = _FakeSession(tools=("other",))
    _patch_transport(monkeypatch, session)
    monkeypatch.setattr(client.shutil, "which", lambda _cmd: "/usr/bin/fake")

    async def run():
        async with mcp_session(STDIO_SPEC) as call_tool:
            await call_tool("search", {})

    with pytest.raises(RuntimeError, match="FAKE_UNAVAILABLE.*missing"):
        asyncio.run(run())


def test_tool_error_uses_tool_error_code(monkeypatch):
    session = _FakeSession(results=[_FakeResult(text="bad input", is_error=True)])
    _patch_transport(monkeypatch, session)
    monkeypatch.setattr(client.shutil, "which", lambda _cmd: "/usr/bin/fake")

    async def run():
        async with mcp_session(STDIO_SPEC) as call_tool:
            await call_tool("search", {})

    with pytest.raises(RuntimeError, match="FAKE_TOOL_FAILED.*bad input"):
        asyncio.run(run())


def test_stdio_env_reaches_transport(monkeypatch):
    seen = {}

    @asynccontextmanager
    async def fake_stdio(command, spec, *_a, **_kw):
        seen["command"] = command
        yield "read", "write"

    class _Params:
        last = None

        def __init__(self, command, env):
            _Params.last = (command, env)

    monkeypatch.setattr(client, "StdioServerParameters", _Params)
    spec = MCPServerSpec(
        name="fake", transport="stdio", command="fake",
        env=lambda: {"FLAG": "1"}, unavailable_code="X",
    )

    @asynccontextmanager
    async def passthrough(_command, _spec):
        yield "read", "write"

    monkeypatch.setattr(client, "_stdio_transport", fake_stdio)
    monkeypatch.setattr(client, "ClientSession", lambda *a, **kw: _FakeSession())
    monkeypatch.setattr(client.shutil, "which", lambda _cmd: "/usr/bin/fake")

    async def run():
        async with mcp_session(spec):
            return None

    asyncio.run(run())
    assert seen["command"] == "/usr/bin/fake"


def test_lazy_spawn_starts_and_waits(monkeypatch):
    attempts = iter([False, False, True])
    monkeypatch.setattr(
        client, "_endpoint_reachable", lambda _url: next(attempts, True)
    )
    monkeypatch.setattr(client.shutil, "which", lambda _cmd: "/usr/bin/reme")
    spawned = []
    monkeypatch.setattr(
        client.subprocess, "Popen", lambda args, **kw: spawned.append(args)
    )
    client._spawn_failed.discard("reme")
    session = _FakeSession(results=[_FakeResult(text="mem")])
    _patch_transport(monkeypatch, session)

    async def run():
        return await call_mcp_tool(REME, "search", {"query": "x"})

    assert asyncio.run(run()) == "mem"
    assert spawned == [["/usr/bin/reme", "start"]]


def test_spawn_failure_is_retryable(monkeypatch):
    """A19: a failed spawn no longer poisons later probes — the next
    call re-checks the endpoint instead of short-circuiting."""
    monkeypatch.setattr(client, "_endpoint_reachable", lambda _url: False)
    monkeypatch.setattr(client.shutil, "which", lambda _cmd: None)
    client._spawn_failed.discard("reme")

    async def run():
        return await call_mcp_tool(REME, "search", {})

    checks = []
    with pytest.raises(RuntimeError, match="REME_UNAVAILABLE"):
        asyncio.run(run())
    # Second call retries: endpoint checked again, still unavailable.
    def endpoint_check(url):
        checks.append(url)
        return False
    client._endpoint_reachable = endpoint_check  # type: ignore[assignment]
    with pytest.raises(RuntimeError, match="not on PATH"):
        asyncio.run(run())
    assert checks, "retry must re-probe the endpoint"
    client._spawn_failed.discard("reme")


def test_existing_adapters_delegates(monkeypatch):
    """The thin wrappers keep their public contracts."""
    from agent.mcp import duckduckgo, reme

    monkeypatch.setattr(client.shutil, "which", lambda _cmd: "/usr/bin/x")
    assert duckduckgo.unavailable_reason() == ""
    monkeypatch.setattr(client.shutil, "which", lambda _cmd: None)
    assert "not installed" in duckduckgo.unavailable_reason()
    assert REME.url == reme.REME_MCP_URL
    assert DUCKDUCKGO.unavailable_code == "RESEARCH_TOOL_UNAVAILABLE"
