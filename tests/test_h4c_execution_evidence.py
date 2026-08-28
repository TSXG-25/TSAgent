"""H4c: completion and final prose require verified execution evidence."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from agent.effect_truth import (
    enforce_completion_gate,
    record_execution_evidence,
)
from agent.orchestrator.finalizer import Finalizer
from agent.runtime import _build_run_evidence
from agent.task import ExecutionPlan, ExecutionStep, Task
from agent.workflow import ExecutionResult


def test_explicit_code_execution_without_evidence_cannot_complete() -> None:
    state = {"requested_outcomes": ["USER_VISIBLE_OUTPUT", "CODE_EXECUTION"]}

    truth = enforce_completion_gate(state)

    assert truth.can_complete is False
    assert truth.unresolved_requested_outcomes == ("CODE_EXECUTION",)
    assert state["runtime_failure_code"] == "EXECUTION_EVIDENCE_MISSING"
    assert state["runtime_terminal_status"] == "FAILED_TERMINAL"


def test_verified_command_evidence_allows_completion() -> None:
    state = {
        "requested_outcomes": ["USER_VISIBLE_OUTPUT", "COMMAND_EXECUTION"],
        "execution_evidence": [{
            "outcome": "COMMAND_EXECUTION",
            "status": "VERIFIED",
            "source": "ToolExecutor",
        }],
    }

    truth = enforce_completion_gate(state)

    assert truth.can_complete is True
    assert truth.unresolved_requested_outcomes == ()


def test_completed_run_evidence_keeps_requested_execution_fact() -> None:
    evidence = _build_run_evidence(
        {
            "requested_outcomes": ["USER_VISIBLE_OUTPUT", "FILE_MUTATION"],
            "execution_evidence": [{
                "outcome": "FILE_MUTATION",
                "status": "VERIFIED",
                "source": "ExecutionVerifier:write",
            }],
            "plan": [{"id": "task-1", "status": "succeeded"}],
            "runtime_terminal_status": "COMPLETED",
        },
        "已成功写入。",
        1,
        "创建 output/result.txt",
    )

    assert evidence["requires_execution"] is True


def test_only_tool_execution_creates_execution_evidence() -> None:
    task = Task.from_dict({
        "id": "task-1",
        "verb": "execute",
        "target": "1 + 1",
        "target_type": "symbol",
    })
    plan = ExecutionPlan(
        task=task,
        steps=[ExecutionStep(tool="run_python", args={"code": "print(2)"})],
        executor="tool",
    )
    state: dict = {}

    record_execution_evidence(
        state,
        plan,
        ExecutionResult(
            success=True,
            metadata={"executor": "tool", "verifier": "none"},
        ),
    )

    assert state["execution_evidence"][0]["outcome"] == "CODE_EXECUTION"
    assert state["execution_evidence"][0]["status"] == "VERIFIED"


def test_llm_plan_does_not_create_execution_evidence() -> None:
    task = Task.from_dict({
        "id": "task-1",
        "verb": "execute",
        "target": "1 + 1",
        "target_type": "symbol",
    })
    plan = ExecutionPlan(
        task=task,
        steps=[],
        executor="llm",
    )
    state: dict = {}

    record_execution_evidence(
        state,
        plan,
        ExecutionResult(success=True, metadata={"executor": "llm"}),
    )

    assert state.get("execution_evidence", []) == []


def test_finalizer_rejects_actual_execution_claim_without_evidence() -> None:
    class Memory:
        def __init__(self) -> None:
            self.answers: list[str] = []

        def record_full_exchange(self, _user_input: str, answer: str) -> None:
            self.answers.append(answer)

    memory = Memory()
    finalizer = Finalizer(SimpleNamespace(
        session_context=SimpleNamespace(memory_view=memory),
        _timings={},
    ))
    state: dict = {}

    answer = asyncio.run(finalizer.run(
        state,
        "执行 date 命令，原样贴输出",
        "user-a",
        best_answer="已执行 date，输出是 2026-08-13。",
    ))

    assert state["runtime_terminal_status"] == "FAILED_TERMINAL"
    assert state["runtime_failure_code"] == "EXECUTION_EVIDENCE_MISSING"
    assert "已执行 date" not in answer
    assert memory.answers[-1] == answer


def test_finalizer_does_not_claim_a_command_was_created() -> None:
    task = Task.from_dict({
        "id": "task-1",
        "verb": "execute",
        "target": "date",
        "target_type": "text",
        "status": "succeeded",
        "facts": {"output": "Sat Aug 15 03:47:01 CST 2026"},
    })

    answer = Finalizer._deterministic_completion_answer({"plan": [task.to_dict()]})

    assert answer is not None
    assert answer.startswith("已执行 date")
    assert "已创建" not in answer
