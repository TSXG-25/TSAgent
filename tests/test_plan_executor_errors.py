"""ExecutionPlan error normalization tests."""
import asyncio
import pytest
from pydantic import ValidationError

from agent.action_result import ActionResult
from agent.executor.plan_executor import PlanExecutor
from agent.executor.executors.tool import ToolExecutor
from agent.failure import ClassificationSource
from agent.orchestrator.main import ExecutionOrchestrator
from agent.sandbox import _run_local
from agent.services.workspace_service import WorkspaceService
from agent.task import ExecutionPlan, ExecutionStep, Task
from agent.workflow import ExecutionContext


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


def test_empty_timeout_exception_gets_non_empty_diagnostic(monkeypatch):
    executor = PlanExecutor()

    async def fail(_tool_name, _args, **_kwargs):
        raise TimeoutError()

    monkeypatch.setattr(executor, "_exec_tool", fail)
    plan = ExecutionPlan(
        task=Task(id="task-timeout"),
        steps=[ExecutionStep(tool="shell", args={"cmd": "date"}, outputs=["output"])],
    )

    result = asyncio.run(executor.execute(plan))

    assert "TimeoutError: tool step failed" in result["_error"]
    assert result["_error_code"] == "PROVIDER_TIMEOUT"


def test_first_step_argument_validation_proves_no_effect(monkeypatch):
    from agent.executor import plan_executor as module

    invalid_arguments = ValidationError.from_exception_data(
        "ToolArguments",
        [{"type": "missing", "loc": ("cmd",), "input": {}}],
    )

    class _InvalidTool:
        async def ainvoke(self, _args):
            raise invalid_arguments

    monkeypatch.setattr(module.tool_registry, "get", lambda _name: _InvalidTool())
    plan = ExecutionPlan(
        task=Task(id="task-invalid-arguments", verb="execute"),
        steps=[ExecutionStep(
            tool="shell",
            args={"cmd": "date"},
            outputs=["output"],
        )],
    )

    result = asyncio.run(PlanExecutor().execute(plan))

    assert result["_error_code"] == "INVALID_TOOL_ARGUMENT"
    assert result["_side_effect_state"] == "NONE"


def test_argument_validation_after_an_earlier_step_is_unknown(monkeypatch):
    from agent.executor import plan_executor as module

    invalid_arguments = ValidationError.from_exception_data(
        "ToolArguments",
        [{"type": "missing", "loc": ("cmd",), "input": {}}],
    )

    class _InvalidTool:
        async def ainvoke(self, _args):
            raise invalid_arguments

    monkeypatch.setattr(module.tool_registry, "get", lambda _name: _InvalidTool())
    executor = PlanExecutor()

    async def previous_step(_step, _args, _workspace, _variables):
        return {"content": "observed"}

    monkeypatch.setattr(executor, "_exec_workspace", previous_step)
    plan = ExecutionPlan(
        task=Task(id="task-later-invalid-arguments", verb="execute"),
        steps=[
            ExecutionStep(tool="workspace", args={"spec": "."}, outputs=["path"]),
            ExecutionStep(tool="shell", args={"cmd": "date"}, outputs=["output"]),
        ],
    )

    result = asyncio.run(executor.execute(plan))

    assert result["_error_code"] == "INVALID_TOOL_ARGUMENT"
    assert result["_side_effect_state"] == "UNKNOWN"


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


def test_sandbox_unavailable_has_stable_non_retryable_code(monkeypatch, tmp_path):
    monkeypatch.delenv("TSAGENT_ALLOW_LOCAL_EXECUTION", raising=False)

    output = _run_local(
        "date",
        timeout=1,
        cwd=str(tmp_path),
        allow_external_workspace=True,
    )

    assert "EXECUTION_ENVIRONMENT_UNAVAILABLE" in output


def test_structured_environment_failure_is_not_executed_twice(monkeypatch, tmp_path):
    from agent import sandbox

    monkeypatch.delenv("TSAGENT_ALLOW_LOCAL_EXECUTION", raising=False)
    monkeypatch.setattr(sandbox, "_check_docker", lambda: False)
    run_local = sandbox._run_local
    calls = 0

    def counted_run_local(*args, **kwargs):
        nonlocal calls
        calls += 1
        return run_local(*args, **kwargs)

    monkeypatch.setattr(sandbox, "_run_local", counted_run_local)
    task = Task(id="task-date")
    plan = ExecutionPlan(
        task=task,
        steps=[ExecutionStep(tool="shell", args={"cmd": "date"}, outputs=["output"])],
        executor="tool",
    )
    context = ExecutionContext(task=task, user_input="执行 date")
    context.set_var("execution_plan", plan)
    workspace = WorkspaceService.scoped(tmp_path, build_index=False)
    context.set_var("workspace", workspace)

    try:
        result = asyncio.run(ToolExecutor().execute(task, context))
    finally:
        workspace.close()

    assert calls == 1
    assert result.action_result == ActionResult.failure(
        error_code="EXECUTION_ENVIRONMENT_UNAVAILABLE",
        content=result.error,
        classification_source=ClassificationSource.STRUCTURED.value,
        side_effect_state="NONE",
    )
    assert result.action_result.retryable is False

    state, next_state = asyncio.run(ExecutionOrchestrator().observe_failure(
        {
            "runtime_failure_retryable": result.action_result.retryable,
            "runtime_failure": {
                "error_code": result.action_result.error_code,
            },
            "plan": [task.to_dict()],
            "execution_plans": [plan],
            "inbox": {},
        },
        "执行 date",
        "human-test",
    ))

    assert next_state == "FAIL"
    assert state["runtime_failure_code"] == "EXECUTION_ENVIRONMENT_UNAVAILABLE"
    assert calls == 1
