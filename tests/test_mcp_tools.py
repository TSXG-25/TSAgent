"""MCP-discovered tools in the unified loop catalog."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from mcp.types import TextContent

import agent.mcp.client as client
from agent.cognition.effect_authorization import EffectAuthorization
from agent.llm import ToolCall
from agent.loop.runner import AgentLoop
from agent.loop.toolset import build_toolset
from agent.mcp.client import MCPServerSpec
from agent.mcp.tools import (
    ExposedTool,
    clear_tool_cache,
    discover_exposed_tools,
)


EXPOSED_SPEC = MCPServerSpec(
    name="notes",
    title="Notes",
    transport="stdio",
    command="notes-mcp",
    unavailable_code="NOTES_UNAVAILABLE",
    tool_error_code="NOTES_TOOL_FAILED",
    expose_tools=("search", "create"),
)


class _FakeResult:
    def __init__(self, text="", is_error=False):
        self.isError = is_error
        self.content = [TextContent(type="text", text=text)] if text else []
        self.structuredContent = None


class _FakeSession:
    def __init__(self, tools, results=()):
        self._tools = tools
        self._results = list(results)
        self.calls: list[tuple[str, dict]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def initialize(self):
        return None

    async def list_tools(self):
        return SimpleNamespace(tools=list(self._tools))

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if self._results:
            return self._results.pop(0)
        return _FakeResult(text="ok")


def _mcp_tool(name, description="", schema=None):
    return SimpleNamespace(
        name=name,
        description=description,
        inputSchema=schema or {"type": "object", "properties": {}},
    )


@pytest.fixture(autouse=True)
def _clean_cache(monkeypatch, tmp_path):
    # 缓存路径指向临时目录：默认门禁不得读写真实用户目录。
    monkeypatch.setattr(
        "agent.mcp.tools._DISK_CACHE", tmp_path / "mcp_catalog.json"
    )
    clear_tool_cache()
    yield
    clear_tool_cache()


def _patch_transport(monkeypatch, session: _FakeSession):
    @asynccontextmanager
    async def fake_stdio(_command, _spec, *_a, **_kw):
        yield "read", "write"

    monkeypatch.setattr(client, "_stdio_transport", fake_stdio)
    monkeypatch.setattr(client, "ClientSession", lambda *a, **kw: session)
    monkeypatch.setattr(client.shutil, "which", lambda _cmd: "/usr/bin/notes")


def test_discover_returns_only_allowlisted_tools(monkeypatch):
    session = _FakeSession(
        tools=[
            _mcp_tool("search", "搜索笔记", {"type": "object", "properties": {"q": {"type": "string"}}}),
            _mcp_tool("create"),
            _mcp_tool("delete"),  # not in expose_tools → filtered
        ]
    )
    _patch_transport(monkeypatch, session)

    found = asyncio.run(discover_exposed_tools([EXPOSED_SPEC]))

    assert [t.tool_name for t in found] == ["search", "create"]
    assert found[0].catalog_name == "mcp_notes_search"
    assert found[0].description == "搜索笔记"
    assert found[0].input_schema["properties"]["q"]["type"] == "string"


def test_spec_without_expose_tools_never_contacted(monkeypatch):
    spec = MCPServerSpec(name="x", transport="stdio", command="x")
    monkeypatch.setattr(
        client, "list_tools",
        lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("contacted")),
    )
    assert asyncio.run(discover_exposed_tools([spec])) == []


def test_unavailable_server_yields_no_tools(monkeypatch):
    monkeypatch.setattr(client.shutil, "which", lambda _cmd: None)
    assert asyncio.run(discover_exposed_tools([EXPOSED_SPEC])) == []


def test_build_toolset_merges_mcp_tools():
    authorization = SimpleNamespace(
        allows_file_mutation=False,
        requires_execution=False,
        code_execution_allowed=False,
        command_execution_allowed=False,
    )
    exposed = ExposedTool(
        spec=EXPOSED_SPEC,
        tool_name="search",
        description="搜索笔记",
        input_schema={
            "type": "object",
            "properties": {"q": {"type": "string"}},
            "required": ["q"],
        },
    )
    tools = build_toolset(authorization, mcp_tools=[exposed])
    tool = tools["mcp_notes_search"]
    assert tool.executor_tool is None and tool.mcp == (EXPOSED_SPEC, "search")
    assert tool.concurrency_safe
    fn = tool.spec["function"]
    assert fn["description"] == "搜索笔记"
    assert fn["parameters"]["required"] == ["q"]


def _orch(tmp_path):
    from agent.event_bus import EventBus
    from agent.executor.contract import executor_factory  # noqa: F401
    from agent.orchestrator.executor import ExecutionStage
    from agent.services.workspace_service import WorkspaceService

    workspace = WorkspaceService.scoped(
        tmp_path, event_bus=EventBus(), build_index=False,
    )
    orch = SimpleNamespace(
        _timings={},
        run_budget=None,
        run_context=SimpleNamespace(
            workspace=workspace,
            cancellation_view=None,
            event_bus=EventBus(),
        ),
    )
    orch._executor = ExecutionStage(orch)
    return orch


def test_dispatch_routes_namespaced_call_to_server(monkeypatch, tmp_path):
    session = _FakeSession(
        tools=[_mcp_tool("search")], results=[_FakeResult(text="3 notes")]
    )
    _patch_transport(monkeypatch, session)

    orch = _orch(tmp_path)
    state = {"requested_outcomes": []}
    authorization = EffectAuthorization.from_state(state, "查笔记")
    tools = build_toolset(
        authorization,
        mcp_tools=[ExposedTool(EXPOSED_SPEC, "search", "搜索笔记")],
    )
    loop = AgentLoop(orch)
    call = ToolCall(id="c1", name="mcp_notes_search", arguments={"q": "x"})

    msg = asyncio.run(
        loop._dispatch(
            call, tools, state, index=1,
            run_context=orch.run_context, user_id="u", user_input="查笔记",
        )
    )
    assert session.calls == [("search", {"q": "x"})]
    assert msg["content"] == "3 notes"


def test_dispatch_surfaces_mcp_error_as_tool_error(monkeypatch, tmp_path):
    session = _FakeSession(
        tools=[_mcp_tool("search")],
        results=[_FakeResult(text="server exploded", is_error=True)],
    )
    _patch_transport(monkeypatch, session)

    orch = _orch(tmp_path)
    state = {"requested_outcomes": []}
    authorization = EffectAuthorization.from_state(state, "查笔记")
    tools = build_toolset(
        authorization,
        mcp_tools=[ExposedTool(EXPOSED_SPEC, "search", "搜索笔记")],
    )
    loop = AgentLoop(orch)
    call = ToolCall(id="c1", name="mcp_notes_search", arguments={"q": "x"})

    msg = asyncio.run(
        loop._dispatch(
            call, tools, state, index=1,
            run_context=orch.run_context, user_id="u", user_input="查笔记",
        )
    )
    assert "ERROR[NOTES_UNAVAILABLE]" in msg["content"]
    assert "server exploded" in msg["content"]


def test_shipped_specs_exposure_invariant():
    """Registry contract: ddg/reme stay internal; exposed servers carry
    explicit allowlists."""
    from agent.mcp.servers import (
        DUCKDUCKGO, REME, SEQUENTIAL_THINKING, TIME, UTIL,
    )

    assert DUCKDUCKGO.expose_tools == ()
    assert REME.expose_tools == ()
    assert TIME.expose_tools == ("get_current_time", "convert_time")
    assert SEQUENTIAL_THINKING.expose_tools == ("sequentialthinking",)
    assert set(UTIL.expose_tools) == {
        "word_count", "json_format", "regex_findall", "base64_convert",
    }
