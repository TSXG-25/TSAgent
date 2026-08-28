"""H4.1b regressions for execution routing and plan-level authorization."""

import asyncio
from pathlib import Path

from langchain_core.messages import HumanMessage

from agent.compiler.rules import DEFAULT_RULES
from agent.compiler.tool_selector import Compiler
from agent.cognition.cognitive_context import CognitiveContext
from agent.cognition.effect_authorization import EffectAuthorization
from agent.cognition.execution_need import extract_explicit_command
from agent.cognition.intent_engine import IntentEngine
from agent.executor.contract import executor_factory
from agent.orchestrator.executor import ExecutionStage
from agent.orchestrator.planner import (
    _build_code_run_tasks,
    _build_explicit_command_execution_task,
    _build_source_code_execution_task,
)
from agent.task import ExecutionPlan, ExecutionStep, Task
from agent.workflow import ExecutionResult


def _compiler() -> Compiler:
    compiler = Compiler()
    for rule in DEFAULT_RULES:
        compiler.add_rule(rule)
    return compiler


def test_execute_text_is_not_short_circuited_to_llm() -> None:
    task = Task.from_dict({
        "id": "task-date",
        "verb": "execute",
        "target": "date",
        "target_type": "text",
        "goal": "执行 date 命令",
        "policy": {"executor": "llm"},
    })

    plan = _compiler().compile(task)

    assert plan.executor == "tool"
    assert [step.tool for step in plan.steps] == ["shell"]


def test_explicit_command_request_builds_tool_task_without_planner() -> None:
    task = _build_explicit_command_execution_task(
        "执行 date 命令，并原样贴出真实命令输出。"
    )

    assert task is not None
    assert task.verb.value == "execute"
    assert task.target == "date"
    plan = _compiler().compile(task)
    assert plan.executor == "tool"
    assert [step.tool for step in plan.steps] == ["shell"]
    assert extract_explicit_command("执行 date 命令，并原样贴出真实命令输出。") == "date"


def test_code_request_uses_source_direct_execution_without_persistent_write() -> None:
    task = _build_source_code_execution_task(
        "用 Python 计算 1 到 100 的和并实际执行"
    )

    assert task is not None
    plan = _compiler().compile(task)

    assert [step.tool for step in plan.steps] == ["llm", "run_python"]
    assert all(step.tool != "filesystem.write" for step in plan.steps)
    assert task.policy.effect_scope == "USER_EFFECT"


def test_write_python_file_and_run_it_requires_code_execution() -> None:
    outcomes = EffectAuthorization.from_request(
        "把 print('OK') 写入 output/probe.py，并实际运行它、贴出输出"
    ).requested_outcomes

    assert "FILE_MUTATION" in {outcome.value for outcome in outcomes}
    assert "CODE_EXECUTION" in {outcome.value for outcome in outcomes}


def test_literal_python_source_uses_write_then_run_without_llm_content_step() -> None:
    tasks = _build_code_run_tasks(
        "把 print('H9-PROBE-OK') 写入 output/probe.py，并实际运行它、贴出输出。"
    )

    assert tasks is not None
    assert tasks[0].inputs["content"] == "print('H9-PROBE-OK')"
    assert tasks[1].verb.value == "execute"


def test_code_execution_outcome_routes_before_intent_llm() -> None:
    engine = IntentEngine()

    def fail_if_called(*args: object, **kwargs: object) -> object:
        raise AssertionError("explicit code execution must not call intent LLM")

    engine._llm_analyze = fail_if_called  # type: ignore[method-assign]
    intent = engine.analyze(CognitiveContext(
        query="把 print('H9-PROBE-OK') 写入 output/probe.py，并实际运行它、贴出输出。",
    ))

    assert intent.domain == "operation"
    assert intent.action == "execute"
    assert intent.requires_execution is True
    assert intent.target == "output/probe.py"


def test_scoped_python_file_execution_uses_run_workspace(monkeypatch, tmp_path: Path) -> None:
    from agent.event_bus import EventBus
    from agent.executor.plan_executor import PlanExecutor
    from agent.services.workspace_service import WorkspaceService

    workspace = WorkspaceService.scoped(tmp_path, event_bus=EventBus(), build_index=True)
    workspace.write_text("output/probe.py", "print('SCOPED-OK')")

    monkeypatch.setattr(
        "tools.python.run_python_in_workspace",
        lambda code, workspace_root, timeout: "SCOPED-OK",
    )
    task = Task.from_dict({
        "id": "scoped-python",
        "verb": "execute",
        "target": "output/probe.py",
        "target_type": "file",
    })
    plan = ExecutionPlan(
        task=task,
        steps=[ExecutionStep(
            tool="run_python_file",
            args={"path": "output/probe.py"},
            outputs=["output"],
        )],
    )

    result = asyncio.run(PlanExecutor().execute(plan, workspace=workspace))

    assert not result["_error"]
    assert result["output"] == "SCOPED-OK"
    workspace.close()


def test_multi_task_plan_satisfies_write_and_execute_as_a_set() -> None:
    authorization = EffectAuthorization.from_request(
        "在 output/ 下新建 probe.py 并运行"
    )
    tasks = [
        Task.from_dict({
            "id": "write",
            "verb": "write",
            "target": "output/probe.py",
            "target_type": "file",
            "goal": "写入脚本",
        }),
        Task.from_dict({
            "id": "execute",
            "verb": "execute",
            "target": "output/probe.py",
            "target_type": "file",
            "goal": "执行脚本",
        }),
    ]
    plans = [_compiler().compile(task) for task in tasks]

    assert authorization.validate_plan_set(plans) is None
    assert authorization.validate_plan(plans[1]) is not None


def test_plan_set_rejects_missing_required_execution() -> None:
    authorization = EffectAuthorization.from_request(
        "执行 date 命令并原样贴输出"
    )
    task = Task.from_dict({
        "id": "reason",
        "verb": "explain",
        "target": "date",
        "target_type": "text",
        "goal": "解释 date",
    })

    assert authorization.validate_plan_set([_compiler().compile(task)]) is not None


def test_execution_stage_preflights_the_whole_write_execute_chain(monkeypatch) -> None:
    calls: list[str] = []

    class FakeExecutor:
        async def execute(self, task, _context):
            calls.append(task.id)
            return ExecutionResult(
                success=True,
                outputs={"text": "ok"},
                metadata={"executor": "fake", "verifier": "fake"},
            )

    class Orchestrator:
        _timings: dict[str, float] = {}

        def __init__(self, plans):
            self._selector = _compiler()
            self.run_context = None
            self._plans = plans

    request = "在 output/ 下新建 probe.py 并运行"
    write = Task.from_dict({
        "id": "write",
        "verb": "write",
        "target": "output/probe.py",
        "target_type": "file",
        "goal": "写入脚本",
    })
    execute = Task.from_dict({
        "id": "execute",
        "verb": "execute",
        "target": "output/probe.py",
        "target_type": "file",
        "goal": "执行脚本",
    })
    plans = [
        ExecutionPlan(
            task=write,
            steps=[ExecutionStep(
                tool="filesystem.write",
                args={"path": "output/probe.py"},
                outputs=["result"],
            )],
            executor="fake",
        ),
        ExecutionPlan(
            task=execute,
            steps=[ExecutionStep(
                tool="run_python_file",
                args={"path": "output/probe.py"},
                outputs=["output"],
            )],
            executor="fake",
        ),
    ]
    orchestrator = Orchestrator(plans)
    monkeypatch.setitem(executor_factory._registry, "fake", FakeExecutor)
    monkeypatch.setattr(ExecutionStage, "_verify_completion", staticmethod(lambda *_args: ""))

    state = {
        "messages": [HumanMessage(content=request)],
        "plan": [write.to_dict(), execute.to_dict()],
        "execution_plans": plans,
    }
    updated, next_state = asyncio.run(ExecutionStage(orchestrator).run(state))

    assert next_state == "NEXT_TASK"
    assert calls == ["write", "execute"]
    assert all(task["status"] == "succeeded" for task in updated["plan"])
