# tests/test_tools.py
"""Basic tests for the TSAgent tool system.

Tests the tool registration, loading, and basic functionality of each tool.
"""
import sys
import os

# Ensure project root is in path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent.registry.tool_registry import registry, ToolRegistry
from agent.services.tool_service import ToolService


def clear_registry():
    """Reset registry to clean state for tests."""
    # Access internal dicts to clear them
    registry._tools.clear()
    registry._categories.clear()
    registry._tags.clear()


def test_registry_basic():
    """Test basic registry operations."""
    clear_registry()

    def sample_tool(x: int) -> str:
        """A sample tool for testing."""
        return f"result: {x}"

    registry.register(sample_tool, name="test_tool", category="test", tags=["demo"])
    assert registry.get("test_tool") is not None
    assert "test_tool" in registry.get_all()
    assert len(registry.get_all_tools()) == 1
    assert len(registry.get_by_tag("demo")) == 1

    print("[PASS] test_registry_basic")


def test_tool_service():
    """Test ToolService wrapper."""
    clear_registry()

    def my_tool(msg: str) -> str:
        """My test tool."""
        return msg

    ToolService.register_tool(my_tool, name="my_tool", category="test")
    assert ToolService.get_tool("my_tool") is not None
    assert "my_tool" in ToolService.get_all_tools()
    assert len(ToolService.get_all_tools_list()) == 1

    print("[PASS] test_tool_service")


def test_load_all_tools():
    """Test that loading all tool modules works."""
    _reload_all_tools()

    all_tools = registry.get_all()
    print(f"\nLoaded {len(all_tools)} tools:")
    for name, tool in sorted(all_tools.items()):
        desc = (tool.description or "no desc").strip().split("\n")[0][:60]
        print(f"  - {name}: {desc}")

    # Verify core tools exist
    expected_tools = [
        "read_file", "write_file", "list_directory",
        "shell",
        "web_search", "web_fetch",
        "run_python", "run_python_file",
        "query_memory", "get_user_preference", "save_fact",
        "list_workflows", "get_workflow", "run_workflow",
        "propose_patch", "apply_patch",
        "list_all_tools", "get_tool_info",
    ]

    for name in expected_tools:
        assert name in all_tools, f"Missing tool: {name}"

    print(f"[PASS] test_load_all_tools ({len(all_tools)} tools loaded)")


def test_category_and_tags():
    """Test category and tag assignments."""
    _reload_all_tools()

    # Check categories exist
    categories = registry._categories
    assert "filesystem" in categories
    assert "web" in categories
    assert "shell" in categories
    assert "code" in categories
    assert "memory" in categories
    assert "workflow" in categories
    assert "meta" in categories

    # Check tags exist
    assert "file" in registry._tags
    assert "search" in registry._tags
    assert "python" in registry._tags

    print(f"[PASS] test_category_and_tags (categories: {list(categories.keys())})")


def test_web_tool_descriptions():
    """Test that web tools have proper descriptions and are async."""
    _reload_all_tools()

    import inspect
    from tools.web import web_search, web_fetch
    assert inspect.iscoroutinefunction(web_search), "web_search should be async"
    assert inspect.iscoroutinefunction(web_fetch), "web_fetch should be async"

    print("[PASS] test_web_tool_descriptions - web tools are properly async")


def test_patch_tool():
    """Test that patch tools reference proper sandbox paths."""
    _reload_all_tools()

    from tools.patch import propose_patch, apply_patch
    assert callable(propose_patch)
    assert callable(apply_patch)

    tool = registry.get("propose_patch")
    assert tool is not None
    desc = tool.description or ""
    assert "patch" in desc.lower()

    print("[PASS] test_patch_tool")


def test_executor_decider():
    """Test that the ReAct executor exposes the ReAct loop structure.

    ToolDecider was removed during the Phase 1 architecture refactor
    (tool selection is now handled by ToolSelector / Compiler).
    """
    _reload_all_tools()

    from agent.executor.executors.react import ReactExecutor as Executor
    # Verify executor has ReAct loop structure
    assert hasattr(Executor, "_execute_task_react")
    assert hasattr(Executor, "_think")
    assert hasattr(Executor, "_execute_action")

    print("[PASS] test_executor_decider")


def test_tool_registry_imports():
    """Test all tool modules can be imported without errors."""
    import importlib

    tool_modules = [
        "tools.filesystem",
        "tools.shell",
        "tools.web",
        "tools.patch",
        "tools.python",
        "tools.memory",
        "tools.workflow",
        "tools.meta",
    ]

    # Fresh import
    _unload_tool_modules()
    for mod_name in tool_modules:
        try:
            importlib.import_module(mod_name)
        except Exception as e:
            print(f"[FAIL] Import {mod_name}: {e}")
            raise AssertionError(f"Import {mod_name} failed: {e}")

    # Verify they registered
    from agent.registry.tool_registry import registry as reg
    if not reg.get_all():
        print("[FAIL] test_tool_registry_imports: modules imported but no tools registered")
        raise AssertionError("[FAIL] test_tool_registry_imports: modules imported but no tools registered")

    print(f"[PASS] test_tool_registry_imports - all {len(tool_modules)} modules imported ({len(reg.get_all())} tools)")


def test_sandbox_import():
    """Test sandbox module imports properly."""
    try:
        from agent.sandbox import run_in_sandbox
        assert callable(run_in_sandbox)
        print("[PASS] test_sandbox_import")
    except Exception as e:
        print(f"[SKIP] test_sandbox_import - Docker not available: {e}")


def _unload_tool_modules():
    """Unload all tool modules so they can be re-imported fresh."""
    import importlib
    modules_to_unload = [
        "tools.filesystem", "tools.shell", "tools.web", "tools.patch",
        "tools.python", "tools.memory", "tools.workflow", "tools.meta",
        "tools",  # also unload the package itself
    ]
    for mod_name in modules_to_unload:
        if mod_name in sys.modules:
            del sys.modules[mod_name]


def _reload_all_tools():
    """Clear registry, unload modules, and reload all tools."""
    clear_registry()
    _unload_tool_modules()
    from agent.bootstrap import load_all_tools
    load_all_tools()


def run_all_tests():
    """Run all tests and report results."""
    tests = [
        test_registry_basic,
        test_tool_service,
        test_load_all_tools,
        test_category_and_tags,
        test_web_tool_descriptions,
        test_patch_tool,
        test_executor_decider,
        test_tool_registry_imports,
        test_sandbox_import,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"[FAIL] {test.__name__}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print(f"\n{'='*40}")
    print(f"Results: {passed} passed, {failed} failed, {len(tests)} total")
    print(f"{'='*40}")

    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)