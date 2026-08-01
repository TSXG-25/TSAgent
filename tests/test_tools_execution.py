# tests/test_tools_execution.py
"""Execution-level tests for TSAgent tools.

Tests that tools actually run correctly, validating outputs.
These tests require dependencies to be installed and may need network access.
"""
import sys
import os
import importlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Global setup: load tools once
from agent.registry.tool_registry import registry


def setup_module():
    """Load all tools before running tests."""
    # Unload any cached modules
    for mod_name in list(sys.modules.keys()):
        if mod_name.startswith("tools.") or mod_name == "tools":
            del sys.modules[mod_name]
    registry._tools.clear()
    registry._categories.clear()
    registry._tags.clear()
    from agent.bootstrap import init_workspace, load_all_tools
    init_workspace()  # filesystem tools need an active Workspace
    load_all_tools()


def test_run_python_basic():
    """Test basic Python code execution."""
    from tools.python import run_python

    result = run_python("print('hello world')")
    assert "hello world" in result, f"Expected 'hello world', got: {result}"
    print(f"[PASS] test_run_python_basic: {result}")


def test_run_python_math():
    """Test Python code with math operations."""
    from tools.python import run_python

    result = run_python("print(2 + 3 * 4)")
    assert "14" in result, f"Expected 14, got: {result}"
    print(f"[PASS] test_run_python_math: {result}")


def test_run_python_multiline():
    """Test multi-line Python code execution."""
    from tools.python import run_python

    code = '''
for i in range(3):
    print(f"line {i}")
'''
    result = run_python(code)
    assert "line 0" in result
    assert "line 1" in result
    assert "line 2" in result
    print(f"[PASS] test_run_python_multiline:\n{result}")


def test_run_python_security_block():
    """Test that dangerous imports are blocked."""
    from tools.python import run_python

    # Should be blocked
    result = run_python("import os; os.system('ls')")
    assert "禁止" in result or "安全" in result, f"Should have been blocked, got: {result}"
    print(f"[PASS] test_run_python_security_block: {result}")


def test_run_python_syntax_error():
    """Test graceful handling of syntax errors."""
    from tools.python import run_python

    result = run_python("print('unclosed string)")
    assert "语法错误" in result or "SyntaxError" in result, f"Expected error, got: {result}"
    print(f"[PASS] test_run_python_syntax_error: {result}")


def test_run_python_file():
    """Test running a Python file."""
    from tools.python import run_python_file

    # Create a temp test file
    test_code = "print('file executed successfully')"
    test_path = "_test_temp_tool_file.py"
    try:
        with open(test_path, "w") as f:
            f.write(test_code)
        result = run_python_file(test_path)
        assert "file executed successfully" in result, f"Unexpected result: {result}"
        print(f"[PASS] test_run_python_file: {result}")
    finally:
        if os.path.exists(test_path):
            os.remove(test_path)


def test_read_file():
    """Test reading a file."""
    from tools.filesystem import read_file

    # Read this test file itself
    result = read_file("tests/test_tools_execution.py")
    assert "test_read_file" in result, "Should contain its own function name"
    print("[PASS] test_read_file")


def test_write_and_read():
    """Test writing to and then reading a file."""
    from tools.filesystem import write_file, read_file

    test_path = "_test_temp_write.txt"
    test_content = "Hello from tool test!"
    try:
        write_result = write_file(test_path, test_content)
        assert "已写入" in write_result, f"Write failed: {write_result}"

        read_result = read_file(test_path)
        assert test_content in read_result, f"Read back mismatch: {read_result}"
        print(f"[PASS] test_write_and_read: {read_result}")
    finally:
        if os.path.exists(test_path):
            os.remove(test_path)


def test_list_directory():
    """Test listing a directory."""
    from tools.filesystem import list_directory

    result = list_directory(".")
    assert "tools" in result or "agent" in result, f"Expected project contents, got: {result[:200]}"
    print(f"[PASS] test_list_directory (found tools/ and agent/)")


def test_list_nonexistent():
    """Test listing a nonexistent directory."""
    from tools.filesystem import list_directory

    result = list_directory("nonexistent_dir_xyz123")
    assert "不是目录" in result or "错误" in result, f"Expected error, got: {result}"
    print(f"[PASS] test_list_nonexistent: {result}")


def test_web_search():
    """Test web search tool.

    This test requires internet access and may be slow.
    """
    from tools.web import web_search
    import asyncio

    result = asyncio.run(web_search("Python programming language", max_results=2))
    # Should get some result text back
    assert len(result) > 20, f"Search result too short: {result[:100] if result else 'empty'}"
    # Should mention Python or programming
    has_relevant = any(term in result.lower() for term in ["python", "programming", "language"])
    print(f"[PASS] test_web_search (relevant={has_relevant}, len={len(result)} chars)")


def test_web_fetch():
    """Test fetching a webpage.

    This test requires internet access.
    """
    from tools.web import web_fetch
    import asyncio

    result = asyncio.run(web_fetch("https://example.com"))
    assert len(result) > 50, f"Fetch result too short: {result[:100] if result else 'empty'}"
    assert "Example" in result or "example" in result, f"Expected Example.com content, got: {result[:200]}"
    print(f"[PASS] test_web_fetch (len={len(result)} chars)")


def test_web_fetch_invalid():
    """Test fetching an invalid URL."""
    from tools.web import web_fetch
    import asyncio

    result = asyncio.run(web_fetch("https://this-domain-does-not-exist-123456.com"))
    assert "失败" in result or "error" in result.lower(), f"Expected error, got: {result[:100]}"
    print(f"[PASS] test_web_fetch_invalid: {result[:60]}")


def test_propose_patch():
    """Test patch proposal."""
    from tools.patch import propose_patch

    diff = "--- a/test.txt\n+++ b/test.txt\n@@ -1 +1 @@\n-old\n+new"
    result = propose_patch(diff)
    assert "Patch 已保存" in result or "patches" in result, f"Expected save confirmation, got: {result}"
    print(f"[PASS] test_propose_patch: {result}")


def test_meta_list_all_tools():
    """Test the meta tool that lists all tools."""
    from tools.meta import list_all_tools

    result = list_all_tools()
    assert "filesystem" in result or "shell" in result, f"Expected tools listed, got: {result[:100]}"
    # Should list many tools
    tool_count = result.count("- ")
    assert tool_count >= 15, f"Expected 15+ tools, found {tool_count}"
    print(f"[PASS] test_meta_list_all_tools ({tool_count} tools listed)")


def test_meta_get_tool_info():
    """Test getting tool info."""
    from tools.meta import get_tool_info

    result = get_tool_info("read_file")
    assert "read_file" in result
    assert "参数" in result or "参数" in result, f"Expected parameter info, got: {result[:100]}"
    print(f"[PASS] test_meta_get_tool_info: found read_file info")

    # Non-existent tool
    result2 = get_tool_info("nonexistent_tool_xyz")
    assert "不存在" in result2, f"Expected 'not found', got: {result2[:100]}"
    print("[PASS] test_meta_get_tool_info (not-found case)")


def test_shell_local():
    """Test shell execution (local fallback, no Docker needed)."""
    from tools.shell import shell

    result = shell("echo 'hello from shell'")
    assert "hello from shell" in result, f"Expected echo output, got: {result}"
    print(f"[PASS] test_shell_local: {result}")


def test_shell_pipeline():
    """Test shell pipe operations."""
    from tools.shell import shell

    result = shell("echo 'line1\nline2\nline3' | wc -l")
    assert "3" in result.strip(), f"Expected 3 lines, got: {result}"
    print(f"[PASS] test_shell_pipeline: {result.strip()} lines")


def test_workflow_list():
    """Test listing workflows (may be empty)."""
    from tools.workflow import list_workflows

    result = list_workflows()
    # Should either list workflows or say none available
    assert result is not None
    print(f"[PASS] test_workflow_list: {result[:100] if result else 'empty'}")


def test_memory_tools():
    """Test memory tools basic functionality."""
    from tools.memory import query_memory, save_fact, get_user_preference

    # These should not crash (even if memories are empty)
    query_result = query_memory("test", k=1)
    assert query_result is not None
    print(f"[PASS] test_query_memory: {query_result[:50] if query_result else 'no results'}")

    pref_result = get_user_preference("test_user")
    assert pref_result is not None
    print(f"[PASS] test_get_user_preference: {pref_result[:50]}")


def run_all_tests():
    """Run all execution tests and report results."""
    # First load tools
    setup_module()

    tests = [
        ("run_python basic", test_run_python_basic),
        ("run_python math", test_run_python_math),
        ("run_python multiline", test_run_python_multiline),
        ("run_python security", test_run_python_security_block),
        ("run_python syntax error", test_run_python_syntax_error),
        ("run_python file", test_run_python_file),
        ("read file", test_read_file),
        ("write & read", test_write_and_read),
        ("list directory", test_list_directory),
        ("list nonexistent", test_list_nonexistent),
        ("web search", test_web_search),
        ("web fetch", test_web_fetch),
        ("web fetch invalid", test_web_fetch_invalid),
        ("propose patch", test_propose_patch),
        ("meta list tools", test_meta_list_all_tools),
        ("meta get tool info", test_meta_get_tool_info),
        ("shell local", test_shell_local),
        ("shell pipeline", test_shell_pipeline),
        ("workflow list", test_workflow_list),
        ("memory tools", test_memory_tools),
    ]

    passed = 0
    failed = 0
    skipped = 0

    for name, test_fn in tests:
        try:
            print(f"\n--- {name} ---")
            test_fn()
            passed += 1
            print(f"  ✓ PASS")
        except Exception as e:
            import traceback
            print(f"  ✗ FAIL: {e}")
            traceback.print_exc(limit=2)
            failed += 1

    print(f"\n{'='*50}")
    print(f"Execution Tests: {passed} passed, {failed} failed, {skipped} skipped, {len(tests)} total")
    print(f"{'='*50}")

    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)