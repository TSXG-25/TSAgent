from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agent.cognition.cognitive_context import CognitiveContext
from agent.cognition.effect_authorization import EffectAuthorization
from agent.cognition.execution_need import RequestedOutcome
from agent.cognition.intent_engine import IntentEngine
from agent.goal import AcceptanceCriterion, DeliveryRequirement, GoalState, GoalStatus, GoalVerifier


def _intent_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "artifact_required": True,
        "requested_outcomes": ["USER_VISIBLE_OUTPUT", "FILE_MUTATION"],
        "summary": "交付报告",
        "domain": "创作",
        "action": "create",
        "target": "",
        "entities": [],
        "confidence": 0.9,
        "resolved_request": "交付两份内容不同的报告",
        "conversation_relation": "new_request",
        "delivery_requirements": [
            {
                "id": "reports",
                "kind": "file",
                "quantity": 2,
                "target": "",
                "acceptance_criteria": [{"kind": "files_distinct"}],
            }
        ],
        "prohibited_effects": [],
        "acceptance_criteria": [],
        "freshness_required": False,
        "source_grounding_required": False,
    }
    payload.update(overrides)
    return payload


def test_intent_contract_preserves_deliverable_quantity_and_criteria() -> None:
    user_input = "交付两份内容不同的报告"
    intent = IntentEngine()._parse_llm_analysis(
        SimpleNamespace(content=json.dumps(_intent_payload(), ensure_ascii=False)),
        user_input,
        CognitiveContext(query=user_input),
    )

    assert intent.artifact_required is True
    assert len(intent.delivery_requirements) == 1
    assert intent.delivery_requirements[0].quantity == 2
    assert intent.delivery_requirements[0].acceptance_criteria[0].kind == "files_distinct"


def test_artifact_contract_without_delivery_requirement_is_rejected() -> None:
    user_input = "做个计算器"
    with pytest.raises(ValueError, match="artifact_required needs a delivery requirement"):
        IntentEngine()._parse_llm_analysis(
            SimpleNamespace(content=json.dumps(
                _intent_payload(delivery_requirements=[]), ensure_ascii=False
            )),
            user_input,
            CognitiveContext(query=user_input),
        )


def test_goal_contract_version_survives_serialization_and_advances_on_correction() -> None:
    delivery = DeliveryRequirement(
        id="reports",
        kind="file",
        quantity=2,
        acceptance_criteria=(AcceptanceCriterion("files_distinct"),),
    )
    original = GoalState(objective="交付两份报告").with_contract(
        original_request="交付两份报告",
        objective="交付两份报告",
        requested_outcomes=("USER_VISIBLE_OUTPUT", "FILE_MUTATION"),
        delivery_requirements=(delivery,),
        prohibited_effects=(),
        acceptance_criteria=(),
        goal_id="goal-1",
    )
    corrected = original.with_contract(
        original_request="交付两份报告",
        objective="交付两份报告，不要运行其中的程序",
        requested_outcomes=("USER_VISIBLE_OUTPUT", "FILE_MUTATION"),
        delivery_requirements=(delivery,),
        prohibited_effects=("CODE_EXECUTION", "COMMAND_EXECUTION"),
        acceptance_criteria=(),
        goal_id="goal-1",
    )

    restored = GoalState.from_dict(corrected.to_dict())

    assert restored.goal_id == "goal-1"
    assert restored.goal_version == 2
    assert restored.original_request == "交付两份报告"
    assert restored.prohibited_effects == ("CODE_EXECUTION", "COMMAND_EXECUTION")
    assert restored.delivery_requirements == (delivery,)


def test_goal_contract_is_projected_from_durable_conversation_results(tmp_path) -> None:
    from agent.runtime_store import DurableRuntimeStoreView, SqliteRuntimeStore

    store_path = tmp_path / "goal-contract.sqlite"
    store = SqliteRuntimeStore.open(store_path)
    previous = GoalState(
        objective="交付两份报告",
        original_request="交付两份报告",
        goal_id="goal-1",
        goal_version=3,
        delivery_requirements=(DeliveryRequirement("reports", "file", 2),),
    )
    previous_view = DurableRuntimeStoreView(
        store,
        tenant_id="tenant",
        session_id="session",
        run_id="previous",
        request_id="request-previous",
        writer_id="writer-previous",
    )
    previous_view.transition_run_with_event(
        run_status="COMPLETED",
        event_id="event-previous",
        event_type="run_completed",
        timestamp="2026-09-27T00:00:00Z",
        payload={
            "continuation": {
                "goal_contract": previous.to_dict(),
                "acceptance_evidence": [
                    {"deliverable_id": "reports", "criterion": "有标题", "verified": True}
                ],
            }
        },
        run_output={"text": "已完成", "artifact_ids": [], "evidence_ids": []},
    )
    previous_view.close()
    store.close()

    store = SqliteRuntimeStore.open(store_path)
    current_view = DurableRuntimeStoreView(
        store,
        tenant_id="tenant",
        session_id="session",
        run_id="current",
        request_id="request-current",
        writer_id="writer-current",
    )
    try:
        results = current_view.conversation_results()
    finally:
        current_view.close()
        store.close()

    assert results[-1]["goal_contract"] == previous.to_dict()
    assert results[-1]["acceptance_evidence"] == [
        {"deliverable_id": "reports", "criterion": "有标题", "verified": True}
    ]


@pytest.mark.parametrize(
    ("second_report", "complete"),
    [("摘要：乙\n结论：B", True), ("摘要：甲\n结论：A", False)],
)
def test_finalizer_verifies_explicit_file_acceptance_criteria(
    tmp_path, second_report: str, complete: bool
) -> None:
    import hashlib
    from types import SimpleNamespace

    from agent.event_bus import EventBus
    from agent.orchestrator.finalizer import Finalizer
    from agent.services.workspace_service import WorkspaceService

    contents = {
        "output/report-a.md": "摘要：甲\n结论：A",
        "output/report-b.md": second_report,
    }
    workspace = WorkspaceService.scoped(
        tmp_path, event_bus=EventBus(), build_index=False
    )
    try:
        for path, content in contents.items():
            workspace.write_text(path, content)
        requirement = DeliveryRequirement(
            "reports",
            "file",
            2,
            acceptance_criteria=(AcceptanceCriterion("files_distinct"), AcceptanceCriterion("file_contains", text="摘要")),
        )
        state = {
            "goal_state": GoalState(
                objective="交付两份内容不同且包含摘要的报告",
                original_request="交付两份内容不同且包含摘要的报告",
                goal_id="goal-acceptance",
                delivery_requirements=(requirement,),
            ).to_dict(),
            "artifact_required": True,
            "verified_artifact_paths": list(contents),
            "file_artifact_evidence": [
                {
                    "path": path,
                    "digest": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                }
                for path, content in contents.items()
            ],
            "answer_required": True,
            "plan": [],
        }
        finalizer = Finalizer(
            SimpleNamespace(run_context=SimpleNamespace(workspace=workspace))
        )

        result = finalizer._project_goal_answer(state, "报告已处理")
    finally:
        workspace.close()

    assert (state["goal_state"]["status"] == GoalStatus.COMPLETE.value) is complete
    if complete:
        assert result == "报告已处理"
    else:
        assert "目标尚未完成" in result
    assert all(item["verified"] for item in state["acceptance_evidence"]) is complete
    assert ("acceptance:reports:files_distinct" in state["goal_missing"]) is not complete


def test_goal_verifier_does_not_reuse_one_file_for_two_requirements() -> None:
    goal_state = GoalState(
        objective="交付两份报告",
        original_request="交付两份报告",
        goal_id="goal-1",
        delivery_requirements=(
            DeliveryRequirement("report-a", "file", 1),
            DeliveryRequirement("report-b", "file", 1),
        ),
    ).to_dict()
    state = {
        "goal_state": goal_state,
        "artifact_required": True,
        "file_artifact_evidence": [
            {"path": "output/report.md", "digest": "same"},
            {"path": "output/report.md", "digest": "same"},
        ],
        "verified_artifact_paths": ["output/report.md"],
        "answer_required": True,
    }

    decision = GoalVerifier.verify(state, "报告已交付")

    assert decision.status is GoalStatus.FAILED
    assert "deliverable:report-b:1" in decision.missing


def test_goal_verifier_requires_acceptance_evidence_for_explicit_criteria() -> None:
    goal_state = GoalState(
        objective="交付报告",
        original_request="交付报告",
        goal_id="goal-1",
        delivery_requirements=(
            DeliveryRequirement("report", "file", 1, acceptance_criteria=(AcceptanceCriterion("file_contains", text="摘要"),)),
        ),
    ).to_dict()
    state = {
        "goal_state": goal_state,
        "artifact_required": True,
        "file_artifact_evidence": [{"path": "output/report.md", "digest": "d1"}],
        "verified_artifact_paths": ["output/report.md"],
        "acceptance_evidence": [],
        "answer_required": True,
    }

    decision = GoalVerifier.verify(state, "报告已交付")

    assert decision.can_complete is False
    assert "acceptance:report:file_contains:摘要" in decision.missing


def test_authorization_enforces_goal_prohibited_effects() -> None:
    state = {
        "original_request": "写一个脚本，但不要运行",
        "resolved_request": "写脚本",
        "requested_outcomes": [
            RequestedOutcome.USER_VISIBLE_OUTPUT.value,
            RequestedOutcome.FILE_MUTATION.value,
            RequestedOutcome.CODE_EXECUTION.value,
        ],
        "artifact_required": True,
        "goal_state": {"prohibited_effects": ["CODE_EXECUTION"]},
    }

    authorization = EffectAuthorization.from_state(state, "写脚本")

    assert authorization.allows_file_mutation is True
    assert authorization.code_execution_allowed is False
