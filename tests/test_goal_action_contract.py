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
        side_effect_state="NONE",
    )

    assert result.ok is False
    assert result.value is None
    assert result.error_code == "BINARY_FILE"
    assert ActionResult.from_dict(result.to_dict()) == result


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


def test_goal_requires_every_declared_deliverable_instance() -> None:
    state = {
        "goal_state": {
            "objective": "交付两份报告",
            "goal_version": 1,
            "delivery_requirements": [
                {
                    "id": "reports",
                    "kind": "file",
                    "quantity": 2,
                    "target": "",
                    "acceptance_criteria": [],
                }
            ],
        },
        "artifact_required": True,
        "file_artifact_evidence": [{"path": "output/report-a.md", "digest": "a"}],
        "verified_artifact_paths": ["output/report-a.md"],
        "answer_required": True,
    }

    decision = GoalVerifier.verify(state, "已完成")

    assert decision.status is GoalStatus.FAILED
    assert "deliverable:reports:2" in decision.missing


def test_goal_counts_distinct_verified_files_for_deliverable_quantity() -> None:
    state = {
        "goal_state": {
            "objective": "交付两份报告",
            "goal_version": 1,
            "delivery_requirements": [
                {
                    "id": "reports",
                    "kind": "file",
                    "quantity": 2,
                    "target": "",
                    "acceptance_criteria": [],
                }
            ],
        },
        "artifact_required": True,
        "file_artifact_evidence": [
            {"path": "output/report-a.md", "digest": "a"},
            {"path": "output/report-b.md", "digest": "b"},
        ],
        "verified_artifact_paths": ["output/report-a.md", "output/report-b.md"],
        "answer_required": True,
    }

    decision = GoalVerifier.verify(state, "两份报告已交付")

    assert decision.can_complete is True


def test_goal_round_is_bounded() -> None:
    goal = GoalState(objective="修复 bug", max_rounds=1)
    first = goal.next_round()
    exhausted = first.next_round()

    assert first.round == 1
    assert first.status is GoalStatus.ACTIVE
    assert exhausted.status is GoalStatus.FAILED
    assert exhausted.blocker == "GOAL_ROUND_LIMIT"


def _loop_state(tasks):
    return {
        "messages": [HumanMessage(content="读取两个文件")],
        "plan": [task.to_dict() for task in tasks],
        "goal_state": GoalState(objective="读取两个文件").to_dict(),
        "inbox": {"next_step": [], "next_turn": []},
        "execution_mode": "result_driven",
    }


def _action_plan(task, tool="filesystem.read"):
    return ExecutionPlan(
        task=task,
        steps=[ExecutionStep(tool=tool, args={}, outputs=["c"])],
        executor="fake",
    )


class _FakeOrchestrator:
    _timings: dict[str, float] = {}
    run_context = None


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

    monkeypatch.setitem(executor_factory._registry, "tool", FakeExecutor)
    tasks = [
        Task.from_dict({"id": "read-a", "verb": "read", "target": "a.txt", "target_type": "file"}),
        Task.from_dict({"id": "read-b", "verb": "read", "target": "b.txt", "target_type": "file"}),
    ]
    state = _loop_state(tasks)
    stage = ExecutionStage(_FakeOrchestrator())

    asyncio.run(stage.execute_action(state, state["plan"][0], _action_plan(tasks[0])))
    assert calls == ["read-a"]
    assert state["plan"][0]["status"] == "succeeded"
    assert state["plan"][1]["status"] == "pending"
    assert state["inbox"]["next_step"][0]["result"]["ok"] is True

    asyncio.run(stage.execute_action(state, state["plan"][1], _action_plan(tasks[1])))
    assert calls == ["read-a", "read-b"]
    assert all(task["status"] == "succeeded" for task in state["plan"])
    assert len(state["inbox"]["next_step"]) == 2


def test_result_driven_normal_action_failure_is_an_observation(monkeypatch) -> None:
    class FailingExecutor:
        async def execute(self, _task, _context):
            return ExecutionResult(
                success=False,
                error="file not found",
                action_result=ActionResult.failure(
                    error_code="FILE_NOT_FOUND",
                    content="file not found",
                    side_effect_state="NONE",
                ),
                metadata={"executor": "fake"},
            )

    monkeypatch.setitem(executor_factory._registry, "tool", FailingExecutor)
    task = Task.from_dict({
        "id": "read-missing",
        "verb": "read",
        "target": "missing.txt",
        "target_type": "file",
    })
    state = _loop_state([task])

    result = asyncio.run(
        ExecutionStage(_FakeOrchestrator()).execute_action(
            state, state["plan"][0], _action_plan(task),
        )
    )

    assert not result.success
    assert state["plan"][0]["status"] == "failed"
    assert state["runtime_failure_side_effect_state"] == "NONE"
    assert state["inbox"]["next_step"][0]["result"]["error_code"] == "FILE_NOT_FOUND"
