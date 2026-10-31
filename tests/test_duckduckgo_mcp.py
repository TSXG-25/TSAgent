from __future__ import annotations

import asyncio
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp.types import TextContent

from agent.mcp import duckduckgo


def test_quickstart_lock_installs_duckduckgo_mcp_server_and_auto_backend() -> None:
    lock_path = Path(__file__).parents[1] / "requirements-lock.txt"
    locked_packages = {
        line.split("==", 1)[0].lower(): line.split("==", 1)[1]
        for line in lock_path.read_text().splitlines()
        if "==" in line and not line.lstrip().startswith("#")
    }

    assert locked_packages["duckduckgo-mcp-server"] == "0.6.1"
    assert locked_packages["curl-cffi"] == "0.15.0"
    assert locked_packages["beautifulsoup4"] == "4.13.3"
    assert locked_packages["soupsieve"] == "2.7"


class _Session:
    def __init__(self, result: object, tools: tuple[str, ...] = ("search",)) -> None:
        self.result = result
        self.tools = tools
        self.called: tuple[str, dict[str, object]] | None = None

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def initialize(self) -> None:
        return None

    async def list_tools(self) -> SimpleNamespace:
        return SimpleNamespace(tools=[SimpleNamespace(name=name) for name in self.tools])

    async def call_tool(self, name: str, arguments: dict[str, object]) -> object:
        self.called = (name, arguments)
        return self.result


def test_duckduckgo_mcp_discovers_and_calls_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _Session(SimpleNamespace(
        isError=False,
        content=[TextContent(type="text", text="result")],
        structuredContent=None,
    ))
    captured: dict[str, object] = {}

    @asynccontextmanager
    async def fake_stdio(server: object):
        captured["server"] = server
        yield object(), object()

    monkeypatch.setattr(duckduckgo.shutil, "which", lambda _command: "/fake/ddg-mcp")
    monkeypatch.setattr(duckduckgo, "stdio_client", fake_stdio)
    monkeypatch.setattr(duckduckgo, "ClientSession", lambda *_args, **_kwargs: session)

    result = asyncio.run(duckduckgo.call_duckduckgo_tool("search", {"query": "MCP"}))

    assert result == "result"
    assert session.called == ("search", {"query": "MCP"})
    server = captured["server"]
    assert server.command == "/fake/ddg-mcp"
    assert server.env["DDG_REF_URL_THRESHOLD"] == "0"
    assert server.env["DDG_SEARCH_BACKEND"] == "auto"


def test_duckduckgo_mcp_runs_stdio_process_and_closes_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from mcp.client.stdio import StdioServerParameters

    server_script = Path(__file__).parent / "fixtures" / "fake_mcp_stdio_server.py"
    stopped_file = tmp_path / "server-stopped"
    pid_file = tmp_path / "server-pid"

    def local_server_parameters(
        command: str,
        env: dict[str, str],
    ) -> StdioServerParameters:
        return StdioServerParameters(
            command=command,
            args=[str(server_script)],
            env={
                **env,
                "TEST_MCP_PID_FILE": str(pid_file),
                "TEST_MCP_STOP_FILE": str(stopped_file),
            },
        )

    monkeypatch.setattr(duckduckgo.shutil, "which", lambda _command: sys.executable)
    monkeypatch.setattr(duckduckgo, "StdioServerParameters", local_server_parameters)

    result = asyncio.run(
        duckduckgo.call_duckduckgo_tool(
            "search",
            {"query": "offline MCP", "max_results": 2},
        )
    )

    assert "query=offline MCP; max_results=2" in result
    assert "https://example.test/offline" in result
    assert stopped_file.read_text() == "stopped"
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)


def test_duckduckgo_mcp_cancellation_terminates_stdio_process(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from mcp.client.stdio import StdioServerParameters

    server_script = Path(__file__).parent / "fixtures" / "fake_mcp_stdio_server.py"
    pid_file = tmp_path / "server-pid"
    call_file = tmp_path / "tool-call-started"

    def local_server_parameters(
        command: str,
        env: dict[str, str],
    ) -> StdioServerParameters:
        return StdioServerParameters(
            command=command,
            args=[str(server_script)],
            env={
                **env,
                "TEST_MCP_PID_FILE": str(pid_file),
                "TEST_MCP_STOP_FILE": str(tmp_path / "server-stopped"),
                "TEST_MCP_CALL_FILE": str(call_file),
            },
        )

    monkeypatch.setattr(
        duckduckgo,
        "shutil",
        SimpleNamespace(which=lambda _command: sys.executable),
    )
    monkeypatch.setattr(duckduckgo, "StdioServerParameters", local_server_parameters)

    async def cancel_call() -> int:
        task = asyncio.create_task(
            duckduckgo.call_duckduckgo_tool(
                "search",
                {"query": "slow", "max_results": 5},
            )
        )
        for _ in range(500):
            if call_file.exists():
                break
            await asyncio.sleep(0.01)
        assert call_file.read_text() == "started"
        server_pid = int(pid_file.read_text())
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return server_pid

    server_pid = asyncio.run(cancel_call())
    with pytest.raises(ProcessLookupError):
        os.kill(server_pid, 0)


def test_duckduckgo_mcp_reports_missing_server(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(duckduckgo.shutil, "which", lambda _command: None)

    with pytest.raises(RuntimeError, match="RESEARCH_TOOL_UNAVAILABLE.*not installed"):
        asyncio.run(duckduckgo.call_duckduckgo_tool("search", {"query": "MCP"}))

    from tools.web import _duckduckgo_availability

    availability = _duckduckgo_availability()
    assert availability.available is False
    assert availability.reason_code == "RESEARCH_TOOL_UNAVAILABLE"
    assert "not installed" in availability.reason


def test_duckduckgo_mcp_reports_missing_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _Session(SimpleNamespace(isError=False, content=[], structuredContent=None), tools=())

    @asynccontextmanager
    async def fake_stdio(_server: object):
        yield object(), object()

    monkeypatch.setattr(duckduckgo.shutil, "which", lambda _command: "/fake/ddg-mcp")
    monkeypatch.setattr(duckduckgo, "stdio_client", fake_stdio)
    monkeypatch.setattr(duckduckgo, "ClientSession", lambda *_args, **_kwargs: session)

    with pytest.raises(RuntimeError, match="MCP tool is missing: search"):
        asyncio.run(duckduckgo.call_duckduckgo_tool("search", {"query": "MCP"}))


def test_duckduckgo_mcp_surfaces_server_tool_error(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _Session(SimpleNamespace(
        isError=True,
        content=[TextContent(type="text", text="blocked")],
        structuredContent=None,
    ))

    @asynccontextmanager
    async def fake_stdio(_server: object):
        yield object(), object()

    monkeypatch.setattr(duckduckgo.shutil, "which", lambda _command: "/fake/ddg-mcp")
    monkeypatch.setattr(duckduckgo, "stdio_client", fake_stdio)
    monkeypatch.setattr(duckduckgo, "ClientSession", lambda *_args, **_kwargs: session)

    with pytest.raises(RuntimeError, match="MCP tool failed: blocked"):
        asyncio.run(duckduckgo.call_duckduckgo_tool("search", {"query": "MCP"}))


def test_web_search_uses_mcp_and_discloses_time_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    import tools.web as web

    calls: list[tuple[str, dict[str, object]]] = []

    async def fake_call(name: str, arguments: dict[str, object]) -> str:
        calls.append((name, arguments))
        return "search results"

    monkeypatch.setattr(web, "_call_duckduckgo_tool", fake_call)
    result = asyncio.run(web.web_search(query="TSAgent", max_results=5, timeliness="week"))

    assert calls[0][0] == "search"
    assert "TSAgent" in calls[0][1]["query"]
    assert calls[0][1]["max_results"] == 5
    assert "不提供精确时间过滤" in result
    assert result.endswith("search results")


def test_web_news_search_discloses_no_exact_date_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    import tools.web as web

    calls: list[tuple[str, dict[str, object]]] = []

    async def fake_call(name: str, arguments: dict[str, object]) -> str:
        calls.append((name, arguments))
        return "news results"

    monkeypatch.setattr(web, "_call_duckduckgo_tool", fake_call)
    result = asyncio.run(web.web_news_search("technology", days=7))

    assert calls[0][0] == "search"
    assert "technology news since " in calls[0][1]["query"]
    assert "不提供精确时间过滤" in result


def test_web_deep_search_fetches_urls_from_mcp_results(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent.mcp import duckduckgo as client
    import tools.web as web

    calls: list[tuple[str, dict[str, object]]] = []

    async def fake_call(name: str, arguments: dict[str, object]) -> str:
        calls.append((name, arguments))
        if name == "search":
            return "1. Source\n   URL: https://example.com/article\n   Summary: result"
        return "article content"

    @asynccontextmanager
    async def fake_client():
        yield fake_call

    monkeypatch.setattr(client, "duckduckgo_mcp_client", fake_client)
    result = asyncio.run(web.web_deep_search("example topic", fetch_top_n=1))

    assert [name for name, _ in calls] == ["search", "fetch_content"]
    assert calls[1][1]["url"] == "https://example.com/article"
    assert "article content" in result


def test_web_fetch_uses_mcp_content_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    import tools.web as web

    calls: list[tuple[str, dict[str, object]]] = []

    async def fake_call(name: str, arguments: dict[str, object]) -> str:
        calls.append((name, arguments))
        return "page content"

    monkeypatch.setattr(web, "_call_duckduckgo_tool", fake_call)
    result = asyncio.run(web.web_fetch("https://example.com"))

    assert calls == [(
        "fetch_content",
        {"url": "https://example.com", "max_length": 8000, "backend": "auto"},
    )]
    assert result == "page content"
