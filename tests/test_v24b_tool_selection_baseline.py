from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from evals.tool_selection.oracle import load_dataset
from realtest_reports.harness.v24b_tool_selection_baseline import _run_case


class _StructuredProvider:
    def __init__(self, action: dict[str, object]) -> None:
        self.action = action

    def with_structured_output(self, _schema):
        action = self.action

        class Runnable:
            async def ainvoke(self, _messages):
                return dict(action)

        return Runnable()

    async def ainvoke(self, _messages):
        raise AssertionError("raw path must not run")


class _InvalidRawProvider:
    def with_structured_output(self, _schema):
        raise RuntimeError("response_format unsupported")

    async def ainvoke(self, _messages):
        return SimpleNamespace(
            content='```json\n{"kind":"ask","tool":"","args":{},"reason":"x","task_id":""}\n```'
        )


def test_harness_scores_production_selector_without_tool_execution() -> None:
    case = load_dataset()["cases"][0]
    provider = _StructuredProvider({
        "kind": "tool",
        "tool": "filesystem.read",
        "args": {"path": "agent/runtime.py"},
        "reason": "read current task",
        "task_id": "read-runtime",
    })

    record = asyncio.run(
        _run_case(
            case,
            provider=provider,
            provider_name="deepseek",
            case_timeout=1.0,
        )
    )

    assert record["passed"] is True
    assert record["provider_path"] == "SINGLE_PROVIDER"
    assert record["format_path"] == "STRUCTURED_ONLY"
    assert record["normalized_next_action"]["tool"] == "filesystem.read"
    assert record["oracle_result"]["passed"] is True
    assert [call["call_kind"] for call in record["provider_calls"]] == [
        "structured_bind",
        "structured_ainvoke",
    ]


def test_harness_preserves_invalid_raw_output_as_capability_evidence() -> None:
    case = load_dataset()["cases"][0]

    record = asyncio.run(
        _run_case(
            case,
            provider=_InvalidRawProvider(),
            provider_name="deepseek",
            case_timeout=1.0,
        )
    )

    assert record["passed"] is False
    assert record["evaluable"] is True
    assert record["provider_status"] == "SUCCESS_WITH_FORMAT_FALLBACK"
    assert record["format_path"] == "STRUCTURED_TO_RAW_FALLBACK"
    assert record["failure_category"] == "P-CAP"
    assert record["failure_subcategory"] == "SCHEMA_INVALID"
    assert record["normalized_next_action"] is None
    assert json.loads(json.dumps(record["raw_provider_output"])) is not None
