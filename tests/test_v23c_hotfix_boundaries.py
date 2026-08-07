"""Deterministic regression gates for v2.3C-H1."""

from __future__ import annotations

from pathlib import Path
import asyncio
from typing import cast

from agent.cognition.cognitive_context import CognitiveContext
from agent.cognition.intent_engine import IntentEngine
from agent.compiler.rules import DEFAULT_RULES
from agent.compiler.tool_selector import Compiler
from agent.execution_errors import classify_execution_error
from agent.repository.indexer import RepositoryIndexer
from agent.security import is_internal_storage_path
from agent.task import Task
from agent.state import AgentState
from agent.runtime import UniversalAgent
from agent.runtime import _build_repo_context
from agent.workspace.index import ProjectIndex
from agent.orchestrator.planner import PlannerStage


def test_agent_storage_is_not_a_user_artifact() -> None:
    assert is_internal_storage_path("data/semantic_memory/chroma.sqlite3")
    assert is_internal_storage_path(".tsagent/runtime.sqlite")
    assert not is_internal_storage_path("output/report.txt")
    from tools.filesystem import read_file

    assert "PROTECTED_INTERNAL_PATH" in read_file(
        "data/semantic_memory/chroma.sqlite3"
    )


def test_repository_indexer_excludes_internal_storage(tmp_path: Path) -> None:
    indexer = RepositoryIndexer(tmp_path)
    assert indexer._should_ignore(tmp_path / "data" / "semantic_memory" / "chroma.sqlite3")
    assert indexer._should_ignore(tmp_path / ".repo_index" / "chroma.sqlite3")
    assert not indexer._should_ignore(tmp_path / "src" / "market.py")
    assert ProjectIndex(tmp_path)._should_ignore(
        tmp_path / ".tsagent" / "runtime.sqlite"
    )


def test_internal_file_error_is_non_retriable() -> None:
    assert classify_execution_error(
        "PlanExecutor: step 1 failed: 错误：PROTECTED_INTERNAL_PATH"
    ) == "PROTECTED_INTERNAL_PATH"
    assert classify_execution_error("错误：无法解码文件 data/blob.bin") == "UNSUPPORTED_BINARY"


def test_internal_repository_hits_never_reach_planner_context(monkeypatch) -> None:
    monkeypatch.setattr(
        "agent.runtime.RepositoryService.search_similar",
        lambda *args, **kwargs: [
            {"path": "data/semantic_memory/chroma.sqlite3", "content": "binary"},
            {"path": "agent/runtime.py", "content": "safe"},
        ],
    )
    context = _build_repo_context("结合用户偏好")
    assert "chroma.sqlite3" not in context
    assert "agent/runtime.py" in context


def test_deterministic_file_failure_does_not_call_replanner() -> None:
    class OrchestratorStub:
        replan_count = 0

    stage = PlannerStage(OrchestratorStub())
    state = {
        "plan": [{
            "id": "task-1",
            "status": "failed",
            "error": "PROTECTED_INTERNAL_PATH",
            "error_code": "PROTECTED_INTERNAL_PATH",
        }],
    }
    updated, next_state = asyncio.run(
        stage.replan(cast(AgentState, state), "读取内部数据库", "hotfix-user")
    )
    assert next_state == "FAIL"
    assert updated["runtime_failure_code"] == "PROTECTED_INTERNAL_PATH"


def test_fresh_financial_research_requires_sources() -> None:
    intent = IntentEngine().analyze(
        CognitiveContext(query="搜索2026年8月近期值得关注的股票与热点板块")
    )
    assert intent.action == "fresh_research"
    assert intent.freshness_required is True
    assert intent.source_grounding_required is True
    assert intent.requires_execution is True


def test_fresh_research_compiles_to_web_search_not_llm() -> None:
    import tools.web  # register the production web_search tool

    task = Task.from_dict({
        "id": "research-1",
        "verb": "search",
        "target": "近期股票热点",
        "target_type": "text",
        "inputs": {"query": "近期股票热点", "timeliness": "week"},
        "policy": {
            "executor": "tool",
            "tool_policy": {"allow": ["web_search"]},
        },
    })
    compiler = Compiler()
    for rule in DEFAULT_RULES:
        compiler.add_rule(rule)
    plan = compiler.compile(task)
    assert plan.executor == "tool"
    assert [step.tool for step in plan.steps] == ["web_search"]


def test_timing_profile_reports_wall_and_exclusive_time_separately() -> None:
    class OrchestratorStub:
        replan_count = 0

        def get_timings(self):
            return {"plan_llm": 2.0}

    agent = UniversalAgent.__new__(UniversalAgent)
    agent._timings = {"plan": 2.0, "executor": 1.0}
    agent.orchestrator = OrchestratorStub()
    agent._wall_total_seconds = 3.1
    profile = agent._print_timing_summary()
    assert "plan" not in profile["spans"]
    assert profile["exclusive_total"] == 3.0
    assert profile["wall_total"] == 3.1
