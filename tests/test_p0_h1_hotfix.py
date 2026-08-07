"""Deterministic P0-H1 regressions from the acceptance run."""

import asyncio
from types import SimpleNamespace

from agent.compiler.rules import DEFAULT_RULES
from agent.compiler.tool_selector import Compiler
from agent.cognition.cognitive_context import CognitiveContext
from agent.cognition.intent_engine import IntentEngine
from agent.execution_errors import classify_execution_error
from agent.executor.plan_executor import PlanExecutor
from agent.orchestrator.planner import (
    PlannerStage,
    _ensure_explicit_output_write_task,
    _extract_literal_file_write,
)
from agent.task import ExecutionPlan, ExecutionStep, Task


def test_replan_without_classifiable_error_does_not_leak_stop_iteration() -> None:
    orchestrator = SimpleNamespace(replan_count=2)
    state = {
        "plan": [{
            "id": "task-1",
            "status": "failed",
            "error": "coroutine raised StopIteration",
            "error_code": "",
        }],
    }

    updated, next_state = asyncio.run(
        PlannerStage(orchestrator).replan(state, "合并文件", "user")
    )

    assert next_state == "FAIL"
    assert updated["plan"][0]["status"] == "failed"


def test_multi_output_materialization_creates_one_write_task_per_target() -> None:
    request = (
        "搜索近期 Python AI Agent 的主要变化，"
        "生成 output/summary.md 和 output/sources.json 两个文件"
    )
    plan = _ensure_explicit_output_write_task(
        [{"id": "task-1", "verb": "search", "target": "AI Agent", "target_type": "text"}],
        request,
    )

    assert [task["verb"] for task in plan] == ["search", "write", "write"]
    assert [task["target"] for task in plan[1:]] == [
        "output/summary.md",
        "output/sources.json",
    ]
    assert all(task["inputs"]["use_prior_facts"] is True for task in plan[1:])


def test_literal_write_fast_path_compiles_without_llm_step() -> None:
    request = "创建 output/hello.txt，内容为 hello"
    extracted = _extract_literal_file_write(request)
    assert extracted == ("output/hello.txt", "hello")

    target, content = extracted
    task = Task.from_dict({
        "id": "task-1",
        "verb": "write",
        "target": target,
        "target_type": "file",
        "goal": "写入用户提供内容",
        "inputs": {"content": content},
    })
    compiler = Compiler()
    for rule in DEFAULT_RULES:
        compiler.add_rule(rule)
    plan = compiler.compile(task)

    assert [step.tool for step in plan.steps] == ["workspace", "filesystem.write"]


def test_literal_file_write_is_classified_without_intent_llm_fallback() -> None:
    intent = IntentEngine().analyze(
        CognitiveContext(query="创建 output/hello.txt，内容为 hello")
    )

    assert intent.domain == "file"
    assert intent.action == "write"
    assert intent.requires_execution is True


def test_unknown_tool_is_rejected_before_any_plan_step(monkeypatch) -> None:
    from agent.executor import plan_executor as module

    calls: list[str] = []

    class _WriteTool:
        async def ainvoke(self, _args):
            calls.append("write")
            return "ok"

    monkeypatch.setattr(
        module.tool_registry,
        "get",
        lambda name: _WriteTool() if name == "write_file" else None,
    )
    plan = ExecutionPlan(
        task=Task(id="task-unknown-tool"),
        steps=[
            ExecutionStep(tool="filesystem.write", args={"path": "x", "content": "x"}, outputs=["result"]),
            ExecutionStep(tool="copy_file", args={"source": "x", "target": "y"}, outputs=["result2"]),
        ],
    )

    result = asyncio.run(PlanExecutor().execute(plan))

    assert result["_error_code"] == "UNKNOWN_TOOL"
    assert calls == []
    assert classify_execution_error(result["_error"]) == "UNKNOWN_TOOL"


def test_provider_failures_have_stable_codes() -> None:
    assert classify_execution_error(TimeoutError()) == "PROVIDER_TIMEOUT"
    assert classify_execution_error("response_format rejected with HTTP 400") == (
        "PROVIDER_REQUEST_INVALID"
    )
