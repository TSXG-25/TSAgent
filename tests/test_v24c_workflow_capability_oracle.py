from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from agent.workflow_decision import WorkflowDecision, WorkflowDecisionKind
from evals.workflow_capability.oracle import (
    dataset_hash,
    evaluate_decision,
    golden_decision,
    golden_self_check,
    load_dataset,
    validate_dataset,
)


DATASET_HASH = "43338803cbe9192c19a2957887a8013c17058a6dbea9e7bb6cb66c06d60fbd69"


def test_workflow_decision_envelopes_are_mutually_exclusive() -> None:
    instantiate = WorkflowDecision(
        kind=WorkflowDecisionKind.INSTANTIATE,
        workflow_id="code_generation",
        bindings={"question_path": "input/q.md"},
    )
    reuse = WorkflowDecision(
        kind=WorkflowDecisionKind.REUSE,
        workflow_id="code_generation",
    )
    decline = WorkflowDecision(kind=WorkflowDecisionKind.DECLINE)

    assert instantiate.to_dict()["kind"] == "instantiate"
    assert reuse.bindings == {}
    assert decline.workflow_id == ""
    with pytest.raises(ValidationError):
        WorkflowDecision(
            kind=WorkflowDecisionKind.REUSE,
            workflow_id="code_generation",
            bindings={"question_path": "replacement"},
        )
    with pytest.raises(ValidationError):
        WorkflowDecision(
            kind=WorkflowDecisionKind.DECLINE,
            workflow_id="code_generation",
        )


def test_frozen_dataset_and_hash_are_valid() -> None:
    dataset = load_dataset()

    assert validate_dataset(dataset) == ()
    assert dataset_hash(dataset) == DATASET_HASH
    assert len(dataset["cases"]) == 24
    assert {case["family"] for case in dataset["cases"]} == {
        "CLEAR_MATCH",
        "FALSE_MATCH_GUARD",
        "PARAMETER_BINDING",
        "SIMPLE_TASK_DECLINE",
        "CONTINUATION",
        "RUNTIME_BOUNDARY",
    }


def test_golden_self_check_is_24_of_24_without_safety_violations() -> None:
    report = golden_self_check()

    assert report == {
        "total": 24,
        "passed": 24,
        "false_workflow_selection": 0,
        "unsafe_reuse": 0,
    }


def test_oracle_detects_false_workflow_selection() -> None:
    dataset = load_dataset()
    case = next(item for item in dataset["cases"] if item["id"] == "C005")
    result = evaluate_decision(dataset, case, {
        "kind": "instantiate",
        "workflow_id": "code_generation",
        "bindings": {
            "question_path": "invented.md",
            "output_path": "output/solution.py",
        },
        "reason": "force a template",
    })

    assert not result["passed"]
    assert result["false_workflow_selection"] == 1
    assert result["safe_decision"] is True


def test_oracle_rejects_unsafe_reuse_even_when_workflow_id_matches() -> None:
    dataset = load_dataset()
    case = next(item for item in dataset["cases"] if item["id"] == "C018")
    result = evaluate_decision(dataset, case, {
        "kind": "reuse",
        "workflow_id": "release_validation",
        "bindings": {},
        "reason": "ignore the blocked projection",
    })

    assert not result["passed"]
    assert not result["safe_decision"]
    assert result["unsafe_reuse"] == 1


def test_dataset_validation_rejects_unknown_expected_workflow() -> None:
    dataset = deepcopy(load_dataset())
    dataset["cases"][0]["expected"]["workflow_id"] = "missing"

    errors = validate_dataset(dataset)

    assert any("expected workflow is unavailable" in error for error in errors)


def test_golden_decision_uses_only_frozen_expected_fields() -> None:
    dataset = load_dataset()
    case = dataset["cases"][0]

    assert golden_decision(case) == {
        "kind": "instantiate",
        "workflow_id": "code_generation",
        "bindings": {
            "question_path": "input/question.docx",
            "output_path": "output/answer.py",
        },
        "reason": "dataset self-check",
    }
