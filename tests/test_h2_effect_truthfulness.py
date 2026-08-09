"""Deterministic v2.3H2 Unsupported Effect Truthfulness contract tests."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from agent.effect_truth import (
    EffectClass,
    detect_requested_effects,
    enforce_completion_gate,
    initialize_effect_contract,
    record_effect_result,
)
from agent.orchestrator.finalizer import Finalizer
from agent.orchestrator.planner import PlannerStage
from agent.runtime import _build_run_evidence
from agent.service.runtime_launcher import RuntimeExecutionLauncher
from agent.task import ExecutionPlan, Task


class _Memory:
    def __init__(self) -> None:
        self.exchanges: list[tuple[str, str]] = []

    def record_full_exchange(self, user_input: str, answer: str) -> None:
        self.exchanges.append((user_input, answer))


def _finalizer() -> tuple[Finalizer, _Memory]:
    memory = _Memory()
    orchestrator = SimpleNamespace(
        session_context=SimpleNamespace(memory_view=memory),
        _timings={},
    )
    return Finalizer(orchestrator), memory


def _reservation_state() -> dict:
    state: dict = {}
    initialize_effect_contract(
        state,
        "帮我订一张明天从北京到上海的机票",
        capability_resolver=lambda _capability, _context: None,
    )
    return state


def test_effect_classifier_distinguishes_external_action_from_information() -> None:
    action = detect_requested_effects("帮我订一张明天从北京到上海的机票")
    question = detect_requested_effects("如何预订机票")

    assert len(action) == 1
    assert action[0].effect_class is EffectClass.EXTERNAL_EFFECT
    assert action[0].capability == "reservation"
    assert question == ()


def test_unsupported_reservation_is_blocked_before_open_ended_planning() -> None:
    async def scenario() -> None:
        orchestrator = SimpleNamespace(
            _context_builder=SimpleNamespace(
                render_context=lambda _context, _now: "",
            )
        )
        state, next_state, answer = await PlannerStage(orchestrator).run(
            "帮我订一张明天从北京到上海的机票",
            "user-a",
            {},
            "",
            "",
        )
        assert next_state == "FINISH"
        assert state["runtime_terminal_status"] == "BLOCKED"
        assert state["runtime_failure_code"] == "UNSUPPORTED_CAPABILITY"
        assert state["required_effects"]
        assert state["unsupported_effects"]
        assert answer is not None
        assert "已预订" not in answer

    asyncio.run(scenario())


def test_finalizer_rejects_llm_success_claim_without_effect_evidence() -> None:
    finalizer, memory = _finalizer()
    state = _reservation_state()
    state["plan"] = [{
        "id": "task-1",
        "verb": "execute",
        "target": "reservation",
        "status": "succeeded",
        "inputs": {},
    }]

    answer = asyncio.run(finalizer.run(
        state,
        "帮我订一张明天从北京到上海的机票",
        "user-a",
        best_answer="已预订成功，订单号为 ABC123。",
    ))

    assert state["runtime_terminal_status"] == "BLOCKED"
    assert state["runtime_failure_code"] == "UNSUPPORTED_CAPABILITY"
    assert "已预订成功" not in answer
    assert "没有可用的预订能力" in answer
    assert memory.exchanges[-1][1] == answer


def test_unverified_known_effect_cannot_complete() -> None:
    state = {
        "required_effects": [{
            "effect_id": "external:reservation",
            "effect_class": "EXTERNAL_EFFECT",
            "capability": "reservation",
            "target": "reservation",
            "description": "预订机票",
        }],
        "verified_effects": [],
    }

    truth = enforce_completion_gate(state)

    assert truth.can_complete is False
    assert state["runtime_terminal_status"] == "FAILED_TERMINAL"
    assert state["runtime_failure_code"] == "UNVERIFIED_EFFECT"
    assert state["unresolved_required_effects"]


def test_one_verified_and_one_unsupported_effect_stays_blocked() -> None:
    state = {
        "required_effects": [
            {
                "effect_id": "external:message_send",
                "effect_class": "EXTERNAL_EFFECT",
                "capability": "message_send",
                "target": "message",
                "description": "发送消息",
            },
            {
                "effect_id": "external:reservation",
                "effect_class": "EXTERNAL_EFFECT",
                "capability": "reservation",
                "target": "reservation",
                "description": "预订机票",
            },
        ],
        "verified_effects": [{
            "effect_id": "external:message_send",
            "status": "VERIFIED",
            "source": "ExecutionVerifier:send_message",
        }],
        "unsupported_effects": [{
            "effect_id": "external:reservation",
            "status": "UNSUPPORTED",
            "reason_code": "UNSUPPORTED_CAPABILITY",
        }],
    }

    truth = enforce_completion_gate(state)

    assert truth.can_complete is False
    assert state["runtime_terminal_status"] == "BLOCKED"
    assert [item["effect_id"] for item in state["unresolved_required_effects"]] == [
        "external:reservation"
    ]


def test_committed_verified_effect_is_allowed_on_resume() -> None:
    finalizer, _memory = _finalizer()
    state = {
        "required_effects": [{
            "effect_id": "external:reservation",
            "effect_class": "EXTERNAL_EFFECT",
            "capability": "reservation",
            "target": "reservation",
            "description": "预订机票",
        }],
        "verified_effects": [{
            "effect_id": "external:reservation",
            "status": "VERIFIED",
            "source": "resume_reconciler:COMMITTED",
        }],
        "plan": [],
    }

    answer = asyncio.run(finalizer.run(
        state,
        "帮我订一张明天从北京到上海的机票",
        "user-a",
        best_answer="此前的预订已由系统核验完成。",
    ))

    assert answer == "此前的预订已由系统核验完成。"
    assert state["effect_truth_ok"] is True
    assert "runtime_terminal_status" not in state


def test_only_verifier_backed_task_effect_can_be_recorded() -> None:
    state: dict = {}
    task = {"id": "task-1", "inputs": {"effect_id": "external:reservation"}}
    verified_plan = ExecutionPlan(task=Task.from_dict({
        "id": "task-1",
        "verb": "execute",
        "target": "reservation",
        "target_type": "symbol",
    }), executor="tool")
    verified_result = SimpleNamespace(
        success=True,
        error="",
        metadata={"verifier": "reservation"},
    )

    record_effect_result(state, task, verified_plan, verified_result)
    assert state["verified_effects"][0]["effect_id"] == "external:reservation"

    state = {}
    llm_plan = ExecutionPlan(task=verified_plan.task, executor="llm")
    record_effect_result(state, task, llm_plan, verified_result)
    assert state.get("verified_effects", []) == []


def test_run_evidence_never_reports_completed_with_unresolved_effect() -> None:
    state = _reservation_state()
    state["plan"] = []
    state["runtime_terminal_status"] = "COMPLETED"

    evidence = _build_run_evidence(state, "已预订成功", transitions=1)

    assert evidence["effect_truth_ok"] is False
    assert evidence["terminal_status"] == "BLOCKED"
    assert evidence["terminal_outputs_verified"] is False
    assert evidence["failure_code"] == "UNSUPPORTED_CAPABILITY"


def test_service_launcher_maps_effect_gate_to_blocked_terminal_event() -> None:
    state = _reservation_state()
    state["plan"] = []
    state["runtime_terminal_status"] = "COMPLETED"
    evidence = _build_run_evidence(state, "已预订成功", transitions=1)
    runtime = SimpleNamespace(last_run_evidence=evidence)

    status, event_type, failure_code = RuntimeExecutionLauncher._terminal_outcome(
        runtime
    )

    assert (status, event_type, failure_code) == (
        "BLOCKED",
        "run_blocked",
        "UNSUPPORTED_CAPABILITY",
    )
