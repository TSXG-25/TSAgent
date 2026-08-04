"""ExecutionPlan error normalization tests."""
import asyncio
import pytest

from agent.executor.plan_executor import PlanExecutor
from agent.task import ExecutionPlan, ExecutionStep, Task


class _Tool:
    async def ainvoke(self, _args):
        return "错误：目标不是有效目录"


def test_tool_error_string_becomes_plan_failure(monkeypatch):
    from agent.executor import plan_executor as module

    monkeypatch.setattr(module.tool_registry, "get", lambda _name: _Tool())
    plan = ExecutionPlan(
        task=Task(id="task-1"),
        steps=[ExecutionStep(tool="filesystem.list", args={"path": "tests"}, outputs=["items"])],
    )

    result = asyncio.run(PlanExecutor().execute(plan))

    assert result["_error"]
    assert "不是有效目录" in result["_error"]


def test_workspace_output_never_becomes_file_content(monkeypatch):
    from agent.executor import plan_executor as module

    captured = {}

    class _WriteTool:
        async def ainvoke(self, args):
            captured.update(args)
            return "已写入 output/example.py"

    monkeypatch.setattr(
        module.tool_registry,
        "get",
        lambda name: _WriteTool() if name == "write_file" else None,
    )
    plan = ExecutionPlan(
        task=Task(id="task-write"),
        steps=[
            ExecutionStep(tool="workspace", args={"spec": "output/example.py"}, outputs=["path"]),
            ExecutionStep(
                tool="filesystem.write",
                args={"path": "$path", "content": "print('ok')"},
                outputs=["result"],
            ),
        ],
    )

    result = asyncio.run(PlanExecutor().execute(plan))

    assert not result["_error"]
    assert captured == {"path": "output/example.py", "content": "print('ok')"}
