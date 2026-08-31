from __future__ import annotations

import asyncio

from evals.workflow_capability.oracle import load_dataset
from realtest_reports.harness.v24c_workflow_baseline import _run_case


class _Provider:
    def __init__(self, decision: dict[str, object]) -> None:
        self.decision = decision

    def with_structured_output(self, _schema):
        provider = self

        class Runnable:
            async def ainvoke(self, _messages):
                return dict(provider.decision)

        return Runnable()

    async def ainvoke(self, _messages):
        raise AssertionError("raw fallback is not expected")


def test_baseline_calls_production_selector_without_workflow_execution() -> None:
    dataset = load_dataset()
    case = dataset["cases"][0]
    provider = _Provider({
        "kind": "instantiate",
        "workflow_id": "code_generation",
        "bindings": {
            "question_path": "input/question.docx",
            "output_path": "output/answer.py",
        },
        "reason": "complete match",
    })

    record = asyncio.run(_run_case(
        dataset,
        case,
        provider=provider,
        provider_name="deepseek",
        case_timeout=1.0,
    ))

    assert record["passed"] is True
    assert record["evaluable"] is True
    assert record["provider_path"] == "SINGLE_PROVIDER"
    assert record["format_path"] == "STRUCTURED_ONLY"
    assert record["case_attempts"] == 1


def test_schema_failure_is_planner_capability_not_oracle_failure() -> None:
    dataset = load_dataset()
    case = dataset["cases"][0]
    provider = _Provider({
        "kind": "instantiate",
        "workflow_id": "code_generation",
        "bindings": {},
        "reason": "incomplete",
    })

    record = asyncio.run(_run_case(
        dataset,
        case,
        provider=provider,
        provider_name="deepseek",
        case_timeout=1.0,
    ))

    assert record["passed"] is False
    assert record["evaluable"] is True
    assert record["failure_category"] == "P-CAP"
    assert record["failure_subcategory"] == "BINDINGS_INVALID"
    assert record["oracle_result"]["binding_accuracy"] is False
