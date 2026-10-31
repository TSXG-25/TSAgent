from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from agent.compiler.tool_selector import CompileError, Compiler
from agent.registry.tool_registry import ToolAvailability, ToolRegistry


def _unavailable_web_registry() -> ToolRegistry:
    registry = ToolRegistry()

    async def web_search(query: str) -> str:
        return query

    registry.register(
        web_search,
        name="web_search",
        category="web",
        tags=["search", "web"],
        availability=lambda: ToolAvailability(
            available=False,
            reason_code="RESEARCH_TOOL_UNAVAILABLE",
            reason="duckduckgo-mcp-server is not installed",
        ),
    )
    return registry


def test_tool_availability_is_current_and_unknown_names_fail_fast() -> None:
    registry = ToolRegistry()
    ready = False

    def inspect_availability() -> ToolAvailability:
        if ready:
            return ToolAvailability(available=True)
        return ToolAvailability(
            available=False,
            reason_code="RESEARCH_TOOL_UNAVAILABLE",
            reason="server is missing",
        )

    def web_search(query: str) -> str:
        return query

    registry.register(web_search, availability=inspect_availability)
    assert registry.get_availability("web_search").reason == "server is missing"
    ready = True
    assert registry.get_availability("web_search").available
    with pytest.raises(KeyError):
        registry.get_availability("missing")


def test_context_and_tool_cards_disclose_missing_web_server(monkeypatch) -> None:
    import agent.registry.tool_registry as registry_module
    import agent.services.tool_service as tool_service

    registry = _unavailable_web_registry()
    monkeypatch.setattr(registry_module, "registry", registry)
    monkeypatch.setattr(tool_service, "registry", registry)

    from agent.context.context_service import ContextService
    from agent.services.tool_service import ToolService

    scope = ContextService._build_scope_boundary({"facts": {}})
    cards = ContextService._build_tool_cards_section(
        {"goal": "搜索最新消息", "facts": {}}
    )

    assert "不可用：duckduckgo-mcp-server is not installed" in scope
    assert "web_search" not in cards
    assert "web_search" not in ToolService.rank_tools("搜索", {})
    assert registry.get_by_tag("search") == []
    assert registry.resolve_by_capability(["search"]) == []


def test_compiler_rejects_registered_but_unavailable_tool() -> None:
    registry = _unavailable_web_registry()

    with pytest.raises(CompileError, match="RESEARCH_TOOL_UNAVAILABLE.*server is not installed"):
        Compiler._tool_exists("web_search", registry)


def test_tool_schemas_are_generated_and_availability_gated() -> None:
    registry = _unavailable_web_registry()

    def read_file(path: str, encoding: str = "utf-8") -> str:
        """Read one workspace file.

        Longer description the model does not need.
        """
        return path

    def run_python(code: str, timeout: int = 10) -> str:
        """Run Python code."""
        return code

    registry.register(read_file)
    registry.register(run_python)

    schemas = {entry["name"]: entry for entry in registry.schemas()}

    # Unavailable tools are excluded from the model-facing list.
    assert "web_search" not in schemas
    assert set(schemas) == {"read_file", "run_python"}

    read = schemas["read_file"]
    assert read["description"] == "Read one workspace file."
    assert read["parameters"] == {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "encoding": {"type": "string"},
        },
        "required": ["path"],
    }

    run = schemas["run_python"]
    assert run["parameters"]["properties"]["timeout"] == {"type": "integer"}
    assert run["parameters"]["required"] == ["code"]
    # Only the model-facing contract is exposed.
    assert set(read) == {"name", "description", "parameters"}


def test_fresh_research_blocks_before_planning_when_mcp_server_is_missing(
    monkeypatch,
) -> None:
    import agent.orchestrator.planner as planner
    from agent.cognition.execution_need import RequestedOutcome
    from agent.cognition.intent_schema import DOMAIN_WEB, IntentResult

    class IntentEngine:
        async def analyze_async(self, _context):
            return IntentResult(
                domain=DOMAIN_WEB,
                action="fresh_research",
                requires_execution=True,
                summary="fresh research",
                raw_input="查一下今天的 AI 新闻",
                resolved_request="查一下今天的 AI 新闻",
                freshness_required=True,
                source_grounding_required=True,
                requested_outcomes=(RequestedOutcome.USER_VISIBLE_OUTPUT,),
            )

    context_builder = SimpleNamespace(
        render_system_context=lambda _now: "",
        render_untrusted_context=lambda _value: "",
        build=lambda **_kwargs: SimpleNamespace(
            query="",
            resolved_query="",
            runtime_pending_target="",
            run_results=[],
            conversation=[],
            short_summary=lambda: "test context",
        ),
        update_conversation_state=lambda *_args, **_kwargs: None,
    )
    resolver = SimpleNamespace(
        resolve=lambda *_args: SimpleNamespace(
            target="",
            symbol="",
            resolution_trace="",
            to_resolved_query=lambda: "",
        )
    )
    orchestrator = SimpleNamespace(
        _context_builder=context_builder,
        _reference_resolver=resolver,
        _conversation_state=SimpleNamespace(),
        session_context=SimpleNamespace(
            memory_view=SimpleNamespace(),
            conversation_retriever=SimpleNamespace(
                runtime_pending=lambda _user_id: False,
                snapshot=lambda _user_id: SimpleNamespace(),
            ),
        ),
        _timings={},
    )
    monkeypatch.setattr(planner, "intent_engine", IntentEngine())
    monkeypatch.setattr(planner, "_tool_registry", _unavailable_web_registry())
    loaded_modules = []
    monkeypatch.setattr(
        "agent.bootstrap.load_tool_modules",
        lambda names: loaded_modules.append(names),
    )

    state, next_state, answer = asyncio.run(
        planner.PlannerStage(orchestrator).run(
            "查一下今天的 AI 新闻",
            "user-a",
            {},
            "",
            "",
        )
    )

    assert next_state == "FINISH"
    assert state["runtime_terminal_status"] == "BLOCKED"
    assert state["runtime_failure_code"] == "RESEARCH_TOOL_UNAVAILABLE"
    assert state["plan"][0]["error_code"] == "RESEARCH_TOOL_UNAVAILABLE"
    assert "duckduckgo-mcp-server is not installed" in answer
    assert loaded_modules == [("web",)]
