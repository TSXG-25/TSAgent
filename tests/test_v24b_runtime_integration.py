from __future__ import annotations

import asyncio

import pytest
from langchain_core.messages import HumanMessage

from agent.action_result import ActionResult
from agent.execution_ownership import (
    ExecutionOwner,
    configure_dynamic_execution,
    resolve_execution_ownership,
)
from agent.executor.contract import executor_factory
from agent.executor.executors.tool import ToolExecutor
from agent.next_action import ActionKind, NextAction
from agent.orchestrator.executor import ExecutionStage
from agent.registry.tool_registry import ToolRegistry
from agent.task import ExecutionPlan, ExecutionStep, Task
from agent.workflow import ExecutionResult


class _Compiler:
    def __init__(self) -> None:
        self.calls = 0

    def compile(self, task: Task, context=None) -> ExecutionPlan:
        self.calls += 1
        return ExecutionPlan(
            task=task,
            steps=[ExecutionStep(tool="compiled.probe", args={"value": "compiled"})],
            executor="tool",
        )


class _SequenceSelector:
    def __init__(self, *actions: NextAction) -> None:
        self.actions = list(actions)
        self.calls = 0

    async def select(self, task, state, observation):
        action = self.actions[self.calls]
        self.calls += 1
        return action


class _CapturingToolExecutor:
    plans: list[ExecutionPlan] = []
    verified = True

    async def execute(self, task, context):
        plan = context.get_var("execution_plan")
        self.plans.append(plan)
        return ExecutionResult(
            success=True,
            outputs={"text": "probe result"},
            action_result=ActionResult.success(
                content="probe result",
                verified=self.verified,
            ),
            metadata={"executor": "tool", "tools_called": [plan.steps[0].tool]},
        )


class _Orchestrator:
    def __init__(self, compiler, selector) -> None:
        self._selector = compiler
        self._next_action_selector = selector
        self._timings = {}
        self.run_context = None


def _registry() -> ToolRegistry:
    registry = ToolRegistry()

    def dynamic_probe(value: str) -> str:
        return value

    def compiled_probe(value: str) -> str:
        return value

    def write_file(path: str, content: str) -> str:
        return path

    registry.register(dynamic_probe, name="dynamic.probe")
    registry.register(compiled_probe, name="compiled.probe")
    registry.register(write_file, name="write_file")
    return registry


def _state(*, dynamic: bool) -> dict:
    state = {
        "messages": [HumanMessage(content="inspect the projected state")],
        "plan": [{
            "id": "task-1",
            "verb": "read",
            "target": "projected state",
            "target_type": "text",
            "goal": "inspect one value",
            "status": "pending",
            "observations": [],
        }],
        "execution_plans": [],
        "execution_mode": "result_driven",
        "inbox": {},
    }
    if dynamic:
        configure_dynamic_execution(state, "task-1", ("dynamic.probe",))
    return state


def test_compiled_task_uses_whole_plan_without_selector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compiler = _Compiler()
    selector = _SequenceSelector()
    executor = _CapturingToolExecutor
    executor.plans = []
    executor.verified = True
    monkeypatch.setitem(executor_factory._registry, "tool", executor)
    monkeypatch.setattr("agent.orchestrator.executor._tool_registry", _registry())

    updated, next_state = asyncio.run(
        ExecutionStage(_Orchestrator(compiler, selector)).run(
            _state(dynamic=False)
        )
    )

    assert next_state == "NEXT_TASK"
    assert compiler.calls == 1
    assert selector.calls == 0
    assert len(executor.plans[0].steps) == 1
    assert executor.plans[0].steps[0].tool == "compiled.probe"
    assert updated["execution_ownership"]["task-1"]["owner"] == "compiled"


def test_dynamic_task_selects_one_action_and_uses_existing_tool_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compiler = _Compiler()
    selector = _SequenceSelector(NextAction.tool_call(
        "dynamic.probe",
        task_id="task-1",
        args={"value": "dynamic"},
    ))
    executor = _CapturingToolExecutor
    executor.plans = []
    executor.verified = True
    monkeypatch.setitem(executor_factory._registry, "tool", executor)
    monkeypatch.setattr("agent.orchestrator.executor._tool_registry", _registry())

    updated, next_state = asyncio.run(
        ExecutionStage(_Orchestrator(compiler, selector)).run(
            _state(dynamic=True)
        )
    )

    assert next_state == "NEXT_TASK"
    assert compiler.calls == 0
    assert selector.calls == 1
    assert len(executor.plans) == 1
    assert executor.plans[0].executor == "tool"
    assert [step.tool for step in executor.plans[0].steps] == ["dynamic.probe"]
    assert updated["execution_plans"] == [None]
    assert updated["plan"][0]["status"] == "succeeded"


def test_dynamic_action_reaches_existing_plan_executor_and_verifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compiler = _Compiler()
    selector = _SequenceSelector(NextAction.tool_call(
        "dynamic.probe",
        task_id="task-1",
        args={"value": "through-plan-executor"},
    ))
    registry = _registry()
    monkeypatch.setitem(executor_factory._registry, "tool", ToolExecutor)
    monkeypatch.setattr("agent.orchestrator.executor._tool_registry", registry)
    monkeypatch.setattr("agent.executor.plan_executor.tool_registry", registry)

    updated, next_state = asyncio.run(
        ExecutionStage(_Orchestrator(compiler, selector)).run(_state(dynamic=True))
    )

    assert next_state == "NEXT_TASK"
    assert updated["plan"][0]["status"] == "succeeded"
    assert updated["plan"][0]["observations"][0]["tools"] == ["dynamic.probe"]
    assert updated["last_action_result"]["verified"] is True


def test_dynamic_unverified_observation_returns_to_selector_then_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compiler = _Compiler()
    selector = _SequenceSelector(
        NextAction.tool_call(
            "dynamic.probe",
            task_id="task-1",
            args={"value": "observe"},
        ),
        NextAction(kind=ActionKind.ANSWER, reason="answer is ready"),
    )
    executor = _CapturingToolExecutor
    executor.plans = []
    executor.verified = False
    monkeypatch.setitem(executor_factory._registry, "tool", executor)
    monkeypatch.setattr("agent.orchestrator.executor._tool_registry", _registry())
    stage = ExecutionStage(_Orchestrator(compiler, selector))

    state, next_state = asyncio.run(stage.run(_state(dynamic=True)))

    assert next_state == "NEXT_ACTION"
    assert state["plan"][0]["status"] == "running"
    assert state["last_action_result"]["verified"] is False
    state["answer_ready"] = True

    state, next_state = asyncio.run(stage.run(state))

    assert next_state == "NEXT_TASK"
    assert state["plan"][0]["status"] == "succeeded"
    assert compiler.calls == 0
    assert selector.calls == 2
    assert len(executor.plans) == 1


def test_execution_owner_cannot_change_after_resolution() -> None:
    state = _state(dynamic=False)
    tasks = state["plan"]
    resolved = resolve_execution_ownership(state, tasks)
    assert resolved["task-1"].owner is ExecutionOwner.COMPILED

    state["execution_ownership"]["task-1"] = {
        "owner": "dynamic",
        "available_tools": ["dynamic.probe"],
    }

    with pytest.raises(ValueError, match="EXECUTION_OWNER_IMMUTABLE"):
        resolve_execution_ownership(state, tasks)


def test_dynamic_ask_blocks_without_entering_tool_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compiler = _Compiler()
    selector = _SequenceSelector(NextAction(
        kind=ActionKind.ASK,
        reason="required input is missing",
    ))
    executor = _CapturingToolExecutor
    executor.plans = []
    monkeypatch.setitem(executor_factory._registry, "tool", executor)
    monkeypatch.setattr("agent.orchestrator.executor._tool_registry", _registry())

    updated, next_state = asyncio.run(
        ExecutionStage(_Orchestrator(compiler, selector)).run(_state(dynamic=True))
    )

    assert next_state == "FAIL"
    assert updated["runtime_terminal_status"] == "BLOCKED"
    assert updated["runtime_failure_code"] == "DYNAMIC_ACTION_NEEDS_INPUT"
    assert executor.plans == []


def test_dynamic_effect_is_authorized_before_shared_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compiler = _Compiler()
    selector = _SequenceSelector(NextAction.tool_call(
        "filesystem.write",
        task_id="task-1",
        args={"path": "output/unrequested.txt", "content": "no"},
    ))
    executor = _CapturingToolExecutor
    executor.plans = []
    monkeypatch.setitem(executor_factory._registry, "tool", executor)
    monkeypatch.setattr("agent.orchestrator.executor._tool_registry", _registry())
    state = _state(dynamic=True)
    state["execution_ownership"]["task-1"]["available_tools"] = [
        "filesystem.write"
    ]

    updated, next_state = asyncio.run(
        ExecutionStage(_Orchestrator(compiler, selector)).run(state)
    )

    assert next_state == "FAIL"
    assert updated["runtime_failure_code"] == "EFFECT_SCOPE_VIOLATION"
    assert executor.plans == []
