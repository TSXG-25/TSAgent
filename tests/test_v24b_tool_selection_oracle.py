"""Deterministic v2.4B Tool Selection contract and Oracle tests."""

from __future__ import annotations

from agent.next_action import ActionKind, NextAction
from evals.tool_selection.oracle import (
    aggregate_metrics,
    dataset_hash,
    evaluate_action,
    golden_action,
    load_dataset,
    validate_dataset,
)


def test_tool_selection_dataset_is_frozen_and_valid() -> None:
    payload = load_dataset()

    assert validate_dataset(payload) == ()
    assert len(payload["cases"]) == 24
    assert len({case["family"] for case in payload["cases"]}) == 6
    assert dataset_hash(payload) == "bc0baa5afcf68ba68a787387edd7297a4c22bea6334e1e0afd06c61136952409"


def test_golden_actions_pass_all_cases() -> None:
    payload = load_dataset()
    reports = [evaluate_action(case, golden_action(case)) for case in payload["cases"]]
    metrics = aggregate_metrics(reports)

    assert metrics["case_count"] == 24
    assert metrics["pass_count"] == 24
    assert metrics["schema_validity"] == 1.0
    assert metrics["safe_action_rate"] == 1.0
    assert metrics["duplicate_effect_count"] == 0
    assert metrics["premature_finish_count"] == 0


def test_oracle_rejects_unknown_tool_and_premature_answer() -> None:
    case = next(case for case in load_dataset()["cases"] if case["id"] == "B001")

    unknown = evaluate_action(
        case,
        NextAction.tool_call(
            "filesystem.delete",
            task_id="read-runtime",
            args={"path": "agent/runtime.py"},
        ).to_dict(),
    )
    premature = evaluate_action(
        case,
        NextAction(kind=ActionKind.ANSWER, tool="", args={}, reason="done", task_id="").to_dict(),
    )

    assert unknown["passed"] is False
    assert "not available" in " ".join(unknown["errors"])
    assert premature["passed"] is False
    assert premature["premature_finish"] is True


def test_oracle_rejects_repeating_verified_effect() -> None:
    case = next(case for case in load_dataset()["cases"] if case["id"] == "B024")
    action = NextAction.tool_call(
        "filesystem.write",
        task_id="write-report",
        args={"path": "output/report.md", "content": "报告内容"},
    )

    report = evaluate_action(case, action)

    assert report["passed"] is False
    assert report["duplicate_effect"] is True
