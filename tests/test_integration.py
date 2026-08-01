"""Unit tests for Phase 1 architecture."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_dag_scheduler():
    """Test DAG scheduling resolves tasks in correct order."""
    from agent.executor.dag import resolve_dag

    tasks = [
        {"id": "task-1", "dependencies": [], "status": "pending", "goal": "A"},
        {"id": "task-2", "dependencies": ["task-1"], "status": "pending", "goal": "B"},
        {"id": "task-3", "dependencies": ["task-1"], "status": "pending", "goal": "C"},
        {"id": "task-4", "dependencies": ["task-2", "task-3"], "status": "pending", "goal": "D"},
    ]

    batches = []
    for batch in resolve_dag(tasks):
        batches.append(batch)
        # resolve_dag 契约：调用方执行 batch 后必须更新任务状态
        for t in batch:
            t["status"] = "succeeded"

    assert len(batches) == 3
    assert [t["goal"] for t in batches[0]] == ["A"]
    assert set(t["goal"] for t in batches[1]) == {"B", "C"}
    assert [t["goal"] for t in batches[2]] == ["D"]
    print("✓ DAG scheduling OK")


def test_dag_deadlock():
    """Test DAG deadlock detection."""
    from agent.executor.dag import resolve_dag

    tasks = [
        {"id": "task-1", "dependencies": ["task-2"], "status": "pending", "goal": "A"},
        {"id": "task-2", "dependencies": ["task-1"], "status": "pending", "goal": "B"},
    ]

    list(resolve_dag(tasks))
    assert tasks[0]["status"] == "failed"
    assert tasks[1]["status"] == "failed"
    print("✓ Deadlock detection OK")


def test_flatten_tree():
    """Test flattening hierarchical tasks into DAG."""
    from agent.executor.dag import flatten_tree

    tasks = [{
        "id": "task-1", "goal": "Parent", "dependencies": [],
        "children": [
            {"id": "task-1-1", "goal": "Child 1", "dependencies": []},
            {"id": "task-1-2", "goal": "Child 2", "dependencies": []},
        ],
    }]

    flat = flatten_tree(tasks)
    assert len(flat) == 3
    child = [t for t in flat if t["id"] == "task-1-1"][0]
    assert "task-1" in child["dependencies"]
    print("✓ Tree flattening OK")


def test_task_schema():
    """Test the new simplified Task Pydantic schema."""
    from agent.planner.schemas import Task, TaskList, Observation

    task = Task(
        id="task-1", goal="测试目标", description="描述",
        success_condition="成功", dependencies=[],
    )
    assert task.id == "task-1"
    assert task.goal == "测试目标"
    assert task.status == "pending"

    tl = TaskList(
        tasks=[task],
        metadata={"reasoning": "测试", "estimated_steps": 1},
    )
    assert len(tl.tasks) == 1

    # Test Observation
    obs = Observation(
        action="read_file", status="succeeded",
        summary="读取文件成功", artifact_ids=["art-1"],
    )
    assert obs.summary == "读取文件成功"
    print("✓ Task schema OK")


def test_artifact_service():
    """Test Artifact Store stores metadata+uri, not content."""
    from agent.services.artifact_service import ArtifactService

    ArtifactService.clear()

    aid = ArtifactService.put(
        artifact_type="code_snippet",
        storage_uri="workspace://login.py",
        summary="发现 login() 函数在第 42 行",
        metadata={"path": "login.py"},
        visibility="final",
    )
    assert aid.startswith("artifact-")

    entry = ArtifactService.get(aid)
    assert entry is not None
    assert entry.type == "code_snippet"
    assert entry.storage_uri == "workspace://login.py"
    assert entry.visibility == "final"

    # get_summary should return formatted string without loading content
    summary = ArtifactService.get_summary(aid)
    assert "code_snippet" in summary
    assert "login()" in summary

    # get_final_artifacts should return this
    finals = ArtifactService.get_final_artifacts()
    assert len(finals) == 1
    print("✓ Artifact Service OK")


def test_tool_registry_capability():
    """Test Tool Registry capability-based resolution."""
    from agent.registry.tool_registry import registry

    # Register test tools with capabilities
    def read_file(path: str) -> str:
        """Read a file."""
        return "file content"

    def search_web(query: str) -> str:
        """Search the web."""
        return "search results"

    registry.register(
        read_file, name="test_read",
        category="fs", tags=["filesystem", "read"],
    )
    registry.register(
        search_web, name="test_search",
        category="web", tags=["internet", "search"],
    )

    # Resolve by capability
    fs_read_tools = registry.resolve_by_capability(["filesystem", "read"])
    assert len(fs_read_tools) > 0
    assert any(t.name == "test_read" for t in fs_read_tools)

    web_tools = registry.resolve_by_capability(["internet"])
    assert len(web_tools) > 0
    assert any(t.name == "test_search" for t in web_tools)

    # Non-matching capability should not return tools
    unmatched = registry.resolve_by_capability(["database"])
    assert len(unmatched) == 0

    print("✓ Tool Registry capability OK")


def test_executor_no_tool_decider():
    """Verify Executor no longer exports ToolDecider."""
    from agent.executor.executors.react import ReactExecutor as Executor

    assert hasattr(Executor, "execute")
    assert not hasattr(Executor, "ToolDecider")  # ToolDecider removed

    # Verify executor has ReAct loop structure
    assert hasattr(Executor, "_execute_task_react")
    assert hasattr(Executor, "_think")
    assert hasattr(Executor, "_execute_action")

    print("✓ Executor ReAct structure OK")


def run_all_tests():
    tests = [
        test_dag_scheduler,
        test_dag_deadlock,
        test_flatten_tree,
        test_task_schema,
        test_artifact_service,
        test_tool_registry_capability,
        test_executor_no_tool_decider,
    ]

    passed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"[FAIL] {test.__name__}: {e}")
            import traceback
            traceback.print_exc()

    print(f"\n{'='*40}")
    print(f"Results: {passed}/{len(tests)} passed")
    print(f"{'='*40}")
    return passed == len(tests)


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
