"""Deterministic evidence semantics for v2.4A Planner acceptance."""

import asyncio
import json
from types import SimpleNamespace

from agent.cognition.cognitive_context import PlannerContext
from evals.planner.dataset_v1_1 import load_dataset_v1_1
from agent.planner import planner as planner_module
from realtest_reports.harness.v24a_planner import (
    _CallRecorder,
    _provider_evidence,
    _provider_status,
)
from realtest_reports.harness.v24a_planner_rebaseline import _p12_planning_context


def _recorder(*calls: tuple[str, str, str]) -> _CallRecorder:
    recorder = _CallRecorder()
    recorder.calls = [
        {
            "sequence": index,
            "provider": provider,
            "call_kind": call_kind,
            "outcome": outcome,
        }
        for index, (provider, call_kind, outcome) in enumerate(calls, start=1)
    ]
    return recorder


def test_same_provider_structured_to_raw_is_format_fallback_only() -> None:
    recorder = _recorder(
        ("deepseek", "structured_bind", "SUCCESS"),
        ("deepseek", "structured_ainvoke", "ERROR"),
        ("deepseek", "router_ainvoke", "SUCCESS"),
    )

    evidence = _provider_evidence(recorder)

    assert evidence["provider_path"] == "SINGLE_PROVIDER"
    assert evidence["format_path"] == "STRUCTURED_TO_RAW_FALLBACK"
    assert _provider_status(recorder, SimpleNamespace(failure_code="")) == (
        "SUCCESS_WITH_FORMAT_FALLBACK"
    )


def test_cross_provider_fallback_is_not_confused_with_format_fallback() -> None:
    recorder = _recorder(
        ("deepseek", "router_ainvoke", "ERROR"),
        ("ollama", "router_ainvoke", "SUCCESS"),
    )

    evidence = _provider_evidence(recorder)

    assert evidence["provider_path"] == "CROSS_PROVIDER_FALLBACK"
    assert evidence["format_path"] == "RAW_ONLY"
    assert _provider_status(recorder, SimpleNamespace(failure_code="")) == (
        "SUCCESS_WITH_PROVIDER_FALLBACK"
    )


def test_single_provider_structured_and_raw_paths_are_explicit() -> None:
    structured = _provider_evidence(
        _recorder(("deepseek", "structured_ainvoke", "SUCCESS"))
    )
    raw = _provider_evidence(
        _recorder(("ollama", "router_ainvoke", "SUCCESS"))
    )

    assert structured["provider_path"] == "SINGLE_PROVIDER"
    assert structured["format_path"] == "STRUCTURED_ONLY"
    assert raw["provider_path"] == "SINGLE_PROVIDER"
    assert raw["format_path"] == "RAW_ONLY"


def test_no_provider_calls_have_no_evidence_path() -> None:
    evidence = _provider_evidence(_CallRecorder())

    assert evidence == {
        "provider_path": "NOT_CALLED",
        "format_path": "NOT_CALLED",
        "provider_sequence": [],
        "resolved_provider_sequence": [],
    }


def test_p12_cases_use_only_runtime_continuation_projection() -> None:
    cases = {
        case["id"]: case
        for case in load_dataset_v1_1()["cases"]
        if case.get("id") in {"PA046", "PA049"}
    }

    pa046 = _p12_planning_context(cases["PA046"])
    assert pa046 is not None
    assert [task["id"] for task in pa046.completed_tasks] == ["g1"]
    assert [task["id"] for task in pa046.continuation_scope] == ["g2"]
    assert pa046.continuation_scope[0]["dependencies"] == []
    assert pa046.plan == []
    assert pa046.task is None

    pa049 = _p12_planning_context(cases["PA049"])
    assert pa049 is not None
    assert [task["id"] for task in pa049.completed_tasks] == ["g1"]
    assert [task["id"] for task in pa049.continuation_scope] == [
        "g2", "g3", "g4",
    ]
    assert [task["dependencies"] for task in pa049.continuation_scope] == [
        [], ["g2"], ["g3"],
    ]
    assert pa049.established_facts == ()
    assert pa049.available_artifacts == ()


def test_production_planner_receives_only_the_continuation_projection(monkeypatch) -> None:
    prompts: list[str] = []

    class FakeLLM:
        supports_structured_output = False

        async def ainvoke(self, messages, **_kwargs):
            prompts.append(str(messages[1].content))
            return SimpleNamespace(content=json.dumps({
                "tasks": [{
                    "id": "task-1",
                    "verb": "write",
                    "target": "output/summary.md",
                    "target_type": "file",
                    "goal": "完成摘要",
                    "dependencies": [],
                    "children": [],
                }],
            }))

    monkeypatch.setattr(planner_module, "llm", FakeLLM())
    context = PlannerContext(
        query="继续完成摘要",
        completed_tasks=({
            "id": "g1", "verb": "read", "target": "input/source.txt",
            "target_type": "file", "status": "succeeded", "dependencies": [],
        },),
        established_facts=("source loaded",),
        available_artifacts=("artifact-a",),
        continuation_scope=({
            "id": "g2", "verb": "write", "target": "output/summary.md",
            "target_type": "file", "status": "pending", "dependencies": [],
        },),
    )

    result = asyncio.run(
        planner_module.plan_with_metadata(
            "继续完成摘要",
            planning_context=context,
        )
    )

    assert result.tasks[0]["target"] == "output/summary.md"
    assert len(prompts) == 1
    assert "g1" in prompts[0]
    assert "output/summary.md" in prompts[0]
    assert "source loaded" in prompts[0]
    assert "artifact-a" in prompts[0]
    assert "checkpoint" not in prompts[0].lower()
