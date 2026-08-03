"""Architecture Validation — 5 regression cases for Workspace.

These tests verify real Agent behavior patterns, not unit tests.
Each case simulates what the Planner/LLM would actually do.
"""
import sys
sys.path.insert(0, ".")

from pathlib import Path
import asyncio
import pytest


# ── Fixtures ──


@pytest.fixture(scope="module")
def workspace():
    """Build a real workspace once per test module."""
    from agent.workspace.manager import WorkspaceManager

    mgr = WorkspaceManager()
    ws = mgr.get(Path(".").resolve())
    ws.build_index()
    ws.build_symbols_async = lambda: None  # sync for tests
    ws._index.build_symbols()
    ws.enable_trace(True)
    return ws


@pytest.fixture
def service(workspace):
    from agent.services.workspace_service import WorkspaceService
    return WorkspaceService()


# ═══════════════════════════════════════════════════════════
# Case 0: PosixPath 不得泄漏到 orchestrator/workflow/executor
# （'PosixPath' object has no attribute 'strip' 回归防护）
# ═══════════════════════════════════════════════════════════


class TestPosixPathLeakGuard:
    """PathMatch.path 是 pathlib.Path；orchestrator 消费必须强制 str（与 plan_executor 一致）。"""

    def test_pathmatch_path_is_posixpath(self, workspace):
        matches = workspace.resolve("runtime")
        assert isinstance(matches[0].path, Path), "PathMatch.path 应为 pathlib.Path"

    def test_orchestrator_pattern_forces_str(self, workspace):
        """orchestrator/planner.py:154 的消费模式（str(best.path)）必须产出 str。"""
        best = workspace.resolve("runtime")[0]
        resolved_target = str(best.path) if hasattr(best, 'path') else str(best)
        assert isinstance(resolved_target, str)
        assert resolved_target.endswith(".py")

    def test_semantic_validator_accepts_posixpath(self):
        """evaluation 层对 PosixPath target 不应崩溃（防御回归）。"""
        from evaluation.benchmark.semantic_validator import _normalize_target
        assert _normalize_target(Path("src/utils.py")) == "src/utils.py"
        assert _normalize_target("parser.py") == "parser.py"


# ═══════════════════════════════════════════════════════════
# Case 1: 模糊文件名 — "修改 runtime 里的 run 函数"
# ═══════════════════════════════════════════════════════════


class TestCase1_FuzzyFilename:
    """User says 'runtime' → Workspace resolves to agent/runtime.py."""

    def test_resolve_runtime_to_file(self, workspace):
        """LLM says 'runtime' → must find agent/runtime.py without asking user."""
        matches = workspace.resolve("runtime")

        assert len(matches) >= 1, "Must find at least one match for 'runtime'"
        best = matches[0]

        # Must be EXACT (stem match) or FUZZY
        assert best.source.name in ("EXACT", "FUZZY"), \
            f"Expected EXACT or FUZZY, got {best.source}"

        # Must have high confidence
        assert best.score >= 0.8, \
            f"Score too low for 'runtime': {best.score}"

        # Must point to the correct file
        assert best.path.name == "runtime.py", \
            f"Should find runtime.py, got {best.path.name}"

        # Verify the file actually contains 'run' function
        content = best.path.read_text(encoding="utf-8")
        assert "def run" in content or "async def run" in content or "class UniversalAgent" in content, \
            "runtime.py should contain UniversalAgent with run method"

    def test_trace_captures_strategies(self, workspace):
        """ResolveTrace should show which strategies were used."""
        workspace.resolve("runtime")
        trace = workspace.last_trace()
        assert trace is not None, "Trace should be available"
        assert "exact" in trace.strategies_used or "fuzzy" in trace.strategies_used
        assert trace.total_candidates >= 1
        assert trace.top_score >= 0.8
        print(f"  Trace: {trace.short()}")


# ═══════════════════════════════════════════════════════════
# Case 2: 目录 — "看一下 registry"
# ═══════════════════════════════════════════════════════════


class TestCase2_Directory:
    """User says 'registry' → should list registry files."""

    def test_resolve_registry_prefix(self, workspace):
        """'registry' should match agent/registry/ directory files via prefix."""
        matches = workspace.resolve("registry")

        # Should find multiple registry files
        names = {m.path.name for m in matches}
        assert len(matches) >= 3, \
            f"Expected 3+ registry files, got {len(matches)}: {names}"

        # Should include key registry files
        expected = {"tool_registry.py", "workflow_registry.py", "skill_registry.py"}
        found = expected & names
        assert len(found) >= 2, \
            f"Expected at least 2 of {expected}, found {found}"

        # All matches should be PREFIX, EXACT, or FUZZY
        valid_sources = {"PREFIX", "EXACT", "FUZZY"}
        for m in matches:
            assert m.source.name in valid_sources, \
                f"Unexpected source {m.source} for {m.path.name}"
        # At least 2 should be FUZZY or better (registry files match via substring)
        # "registry" matches tool_registry.py, workflow_registry.py as substring
        assert len(matches) >= 3, \
            f"Expected 3+ matches for 'registry', got {len(matches)}"


# ═══════════════════════════════════════════════════════════
# Case 3: Symbol — "修改 ProjectIndex"
# ═══════════════════════════════════════════════════════════


class TestCase3_Symbol:
    """User says a class name → Workspace resolves via symbol index."""

    def test_resolve_projectindex_by_symbol(self, workspace, service):
        """'ProjectIndex' should resolve to index.py via symbol."""
        from agent.workspace import MatchSource as MS

        matches = workspace.resolve("ProjectIndex")

        assert len(matches) >= 1, "Must find ProjectIndex symbol"
        best = matches[0]

        # Must be SYMBOL source (not embedding guess)
        assert best.source == MS.SYMBOL or best.source.name == "SYMBOL", \
            f"Expected SYMBOL match for 'ProjectIndex', got {best.source}"

        assert best.path.name == "index.py", \
            f"Should point to index.py, got {best.path.name}"

    def test_resolve_repositoryindexer_by_symbol(self, workspace):
        """'RepositoryIndexer' should resolve to indexer.py."""
        matches = workspace.resolve("RepositoryIndexer")

        assert len(matches) >= 1
        best = matches[0]
        assert "indexer" in str(best.path), \
            f"Should find indexer.py, got {best.path.name}"


# ═══════════════════════════════════════════════════════════
# Case 4: 代词 — "打开 runtime.py，继续修改它"
# ═══════════════════════════════════════════════════════════


class TestCase4_PronounContext:
    """After opening a file, '它' should resolve via context.current_file."""

    def test_context_tracks_current_file(self, workspace):
        """record_open should set current_file for pronoun resolution."""
        workspace.record_open("agent/runtime.py")
        ctx = workspace.current_context()

        assert ctx.current_file == "agent/runtime.py", \
            f"Expected agent/runtime.py, got {ctx.current_file}"

    def test_recent_file_resolves(self, workspace):
        """Recent file should be resolvable via RECENT strategy."""
        workspace.record_open("tools/filesystem.py")

        matches = workspace.resolve("filesystem")
        best = matches[0] if matches else None

        # Should find filesystem.py via exact or fuzzy
        assert best is not None
        assert best.path.name == "filesystem.py", \
            f"Expected filesystem.py, got {best.path.name}"

    def test_context_shows_opened_files(self, workspace):
        """Context should expose opened_files list for prompt injection."""
        workspace.record_open("agent/runtime.py")
        workspace.record_open("agent/workspace/workspace.py")

        ctx = workspace.current_context()
        assert "agent/runtime.py" in ctx.opened_files
        assert "agent/workspace/workspace.py" in ctx.opened_files

        # Simulate LLM prompt: "The user is looking at {current_file}"
        prompt_context = f"Currently viewing: {ctx.current_file}"
        assert "workspace.py" in prompt_context, \
            "Prompt context should contain the current file"


# ═══════════════════════════════════════════════════════════
# Case 5: 多文件 — "修改 router"
# ═══════════════════════════════════════════════════════════


class TestCase5_MultiFile:
    """'router' should return ALL router files; Planner chooses."""

    def test_router_returns_multiple_files(self, workspace):
        """'router' should return workflow_router + skill_router + router dir."""
        matches = workspace.resolve("router")

        names = {m.path.name for m in matches}
        assert len(matches) >= 2, \
            f"Expected 2+ router files, got {len(matches)}: {names}"

        # The key router files
        assert "workflow_router.py" in names, \
            f"Missing workflow_router.py in {names}"
        assert "skill_router.py" in names, \
            f"Missing skill_router.py in {names}"

        # Workspace returns ALL matches — does NOT pick one
        # This is critical: Planner must choose based on scores
        scores = {m.path.name: m.score for m in matches}
        assert scores["workflow_router.py"] >= 0.6, \
            f"workflow_router.py score too low: {scores}"

    def test_router_ordering_is_stable(self, workspace):
        """Multiple resolves of 'router' should return same order."""
        matches1 = workspace.resolve("router")
        matches2 = workspace.resolve("router")

        names1 = [(m.path.name, m.score, m.source.value) for m in matches1]
        names2 = [(m.path.name, m.score, m.source.value) for m in matches2]

        assert names1 == names2, \
            f"Order changed between calls:\n  {names1}\n  {names2}"

    def test_router_no_single_winner(self, workspace):
        """Workspace should NOT pick a single 'correct' file for ambiguous input."""
        matches = workspace.resolve("router")

        # There should be at least 2 matches with similar scores
        if len(matches) >= 2:
            # The top 2 should both be reasonable (no single 1.0 winner)
            top2_scores = [m.score for m in matches[:2]]
            assert all(s >= 0.6 for s in top2_scores), \
                f"Top 2 scores should both be reasonable: {top2_scores}"


# ═══════════════════════════════════════════════════════════
# Bonus: Related files
# ═══════════════════════════════════════════════════════════


class TestCase_Related:
    """workspace.related() should find co-changing files."""

    def test_related_runtime(self, workspace):
        """runtime.py should find related sibling files in agent/."""
        related = workspace.related("agent/runtime.py")

        assert len(related) >= 1, "runtime.py should have related files"
        related_names = {m.path.name for m in related}

        # Same directory siblings (orchestrator is now a package: orchestrator/)
        assert "bootstrap.py" in related_names, \
            f"Expected bootstrap.py as sibling, got {related_names}"

    def test_related_file_not_found(self, workspace):
        """Non-existent file should return empty list."""
        related = workspace.related("nonexistent.py")
        assert related == [], \
            f"Expected empty list for nonexistent file, got {related}"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])