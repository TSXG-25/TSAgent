"""Goal and ActionResult contracts inspired by the harness result model."""

import asyncio

from langchain_core.messages import HumanMessage

from agent.action_result import ActionResult
from agent.goal import GoalState, GoalStatus, GoalVerifier
from agent.executor.contract import executor_factory
from agent.orchestrator.executor import ExecutionStage
from agent.task import ExecutionPlan, ExecutionStep, Task
from agent.workflow import ExecutionResult


def test_action_result_separates_machine_value_from_content() -> None:
    result = ActionResult.success(
        value={"exit_code": 0, "stdout": "42"},
        content="命令输出：42",
        verified=True,
    )

    assert result.ok is True
    assert result.value["exit_code"] == 0
    assert result.content == "命令输出：42"
    assert result.to_dict()["value"]["stdout"] == "42"


def test_failed_action_cannot_carry_success_value() -> None:
    result = ActionResult.failure(
        error_code="BINARY_FILE",
        content="二进制文件不能按文本读取",
    )

    assert result.ok is False
    assert result.value is None
    assert result.error_code == "BINARY_FILE"


def test_goal_requires_evidence_beyond_finished_plan() -> None:
    state = {
        "plan": [{"id": "task-1", "status": "succeeded"}],
        "requested_outcomes": ["USER_VISIBLE_OUTPUT", "CODE_EXECUTION"],
        "execution_evidence": [],
        "required_effects": [],
        "verified_effects": [],
        "unsupported_effects": [],
        "failed_effects": [],
        "answer_required": True,
    }

    decision = GoalVerifier.verify(state, "已完成")

    assert decision.status is GoalStatus.FAILED
    assert decision.can_complete is False
    assert "outcome:CODE_EXECUTION" in decision.missing


def test_goal_completes_with_action_and_answer_evidence() -> None:
    state = {
        "plan": [{"id": "task-1", "status": "succeeded"}],
        "requested_outcomes": ["USER_VISIBLE_OUTPUT", "CODE_EXECUTION"],
        "execution_evidence": [
            {"outcome": "CODE_EXECUTION", "status": "VERIFIED"}
        ],
        "required_effects": [],
        "verified_effects": [],
        "unsupported_effects": [],
        "failed_effects": [],
        "answer_required": True,
    }

    decision = GoalVerifier.verify(state, "执行输出为 42")

    assert decision.status is GoalStatus.COMPLETE
    assert decision.can_complete is True


def test_goal_round_is_bounded() -> None:
    goal = GoalState(objective="修复 bug", max_rounds=1)
    first = goal.next_round()
    exhausted = first.next_round()

    assert first.round == 1
    assert first.status is GoalStatus.ACTIVE
    assert exhausted.status is GoalStatus.FAILED
    assert exhausted.blocker == "GOAL_ROUND_LIMIT"


def test_result_driven_execution_advances_one_action_at_a_time(monkeypatch) -> None:
    calls: list[str] = []

    class FakeExecutor:
        async def execute(self, task, _context):
            calls.append(task.id)
            return ExecutionResult(
                success=True,
                outputs={"text": f"done:{task.id}"},
                action_result=ActionResult.success(
                    value={"task_id": task.id},
                    content=f"done:{task.id}",
                    verified=True,
                ),
                metadata={"executor": "fake", "verifier": "fake"},
            )

    monkeypatch.setitem(executor_factory._registry, "fake", FakeExecutor)
    tasks = [
        Task.from_dict({"id": "read-a", "verb": "read", "target": "a.txt", "target_type": "file"}),
        Task.from_dict({"id": "read-b", "verb": "read", "target": "b.txt", "target_type": "file"}),
    ]
    state = {
        "messages": [HumanMessage(content="读取两个文件")],
        "plan": [task.to_dict() for task in tasks],
        "execution_plans": [
            ExecutionPlan(
                task=task,
                steps=[ExecutionStep(tool="filesystem.read")],
                executor="fake",
            )
            for task in tasks
        ],
        "current_task_index": 0,
        "goal_state": GoalState(objective="读取两个文件").to_dict(),
        "inbox": {"next_step": [], "next_turn": []},
        "execution_mode": "result_driven",
    }

    class Orchestrator:
        _timings: dict[str, float] = {}
        run_context = None

        def __init__(self):
            from agent.compiler.tool_selector import Compiler
            from agent.compiler.rules import DEFAULT_RULES

            self._selector = Compiler()
            for rule in DEFAULT_RULES:
                self._selector.add_rule(rule)

    stage = ExecutionStage(Orchestrator())
    first, first_next = asyncio.run(stage.run(state))
    assert first_next == "NEXT_ACTION"
    assert calls == ["read-a"]
    assert first["plan"][0]["status"] == "succeeded"
    assert first["plan"][1]["status"] == "pending"
    assert first["inbox"]["next_step"][0]["result"]["ok"] is True

    second, second_next = asyncio.run(stage.run(first))
    assert second_next == "NEXT_TASK"
    assert calls == ["read-a", "read-b"]
    assert all(task["status"] == "succeeded" for task in second["plan"])


def test_result_driven_normal_action_failure_is_an_observation(monkeypatch) -> None:
    class FailingExecutor:
        async def execute(self, _task, _context):
            return ExecutionResult(
                success=False,
                error="file not found",
                action_result=ActionResult.failure(
                    error_code="FILE_NOT_FOUND",
                    content="file not found",
                ),
                metadata={"executor": "fake"},
            )

    monkeypatch.setitem(executor_factory._registry, "fake", FailingExecutor)
    task = Task.from_dict({
        "id": "read-missing",
        "verb": "read",
        "target": "missing.txt",
        "target_type": "file",
    })
    state = {
        "messages": [HumanMessage(content="读取文件")],
        "plan": [task.to_dict()],
        "execution_plans": [
            ExecutionPlan(
                task=task,
                steps=[ExecutionStep(tool="filesystem.read")],
                executor="fake",
            )
        ],
        "current_task_index": 0,
        "goal_state": GoalState(objective="读取文件").to_dict(),
        "inbox": {"next_step": [], "next_turn": []},
        "execution_mode": "result_driven",
    }

    class Orchestrator:
        _timings: dict[str, float] = {}
        run_context = None

        def __init__(self):
            from agent.compiler.tool_selector import Compiler
            from agent.compiler.rules import DEFAULT_RULES

            self._selector = Compiler()
            for rule in DEFAULT_RULES:
                self._selector.add_rule(rule)

    updated, next_state = asyncio.run(ExecutionStage(Orchestrator()).run(state))
    assert next_state == "OBSERVE"
    assert not updated.get("runtime_failure_code")
    assert updated["inbox"]["next_step"][0]["result"]["error_code"] == "FILE_NOT_FOUND"
