"""Planner abstain_reason contract: LLM-owned clarification, no regex abstain."""

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import agent.planner.planner as planner_module


def _task(target: str = "output/calculator.py") -> dict:
    return {
        "id": "task-1",
        "verb": "write",
        "target": target,
        "target_type": "file",
        "goal": "创建计算器",
        "description": "写入计算器实现",
        "success_condition": "文件已写入",
        "dependencies": [],
        "children": [],
    }


def _json_llm(captured, payload):
    class FakeLLM:
        supports_structured_output = False

        async def ainvoke(self, messages, **_kwargs):
            captured.append(messages)
            return SimpleNamespace(content=json.dumps(payload, ensure_ascii=False))

    return FakeLLM()


def test_same_sentence_pronoun_does_not_pre_abstain(monkeypatch):
    """PA022 regression: the resolved request must reach the Provider."""
    captured = []
    monkeypatch.setattr(
        planner_module,
        "llm",
        _json_llm(captured, {"tasks": [_task()]}),
    )

    output = asyncio.run(
        planner_module.plan_with_metadata(
            "编写一个计算器，完成后告诉用户它被放在哪个目录下。"
        )
    )

    assert captured, "Provider must be called"
    assert not output.abstain
    assert output.tasks[0]["target"] == "output/calculator.py"


def test_json_abstain_reason_returns_abstain(monkeypatch):
    captured = []
    monkeypatch.setattr(
        planner_module,
        "llm",
        _json_llm(captured, {"tasks": [], "abstain_reason": "缺少目标文件"}),
    )

    output = asyncio.run(planner_module.plan_with_metadata("改一下它"))

    assert output.abstain is True
    assert output.abstain_reason == "缺少目标文件"
    assert output.tasks == []


def test_structured_abstain_reason_returns_abstain(monkeypatch):
    from agent.planner.schemas import TaskList

    class StructuredProvider:
        def with_structured_output(self, _schema):
            async def _empty(_messages):
                return TaskList(tasks=[], abstain_reason="缺少目标文件")

            return SimpleNamespace(ainvoke=_empty)

    class FakeLLM:
        supports_structured_output = True

        def _get_active_provider(self):
            return StructuredProvider(), "fake"

    monkeypatch.setattr(planner_module, "llm", FakeLLM())

    output = asyncio.run(planner_module.plan_with_metadata("改一下它"))

    assert output.abstain is True
    assert output.abstain_reason == "缺少目标文件"


def test_unparseable_output_fails_with_planner_output_invalid(monkeypatch):
    class FakeLLM:
        supports_structured_output = False

        async def ainvoke(self, _messages, **_kwargs):
            return SimpleNamespace(content="not json at all")

    monkeypatch.setattr(planner_module, "llm", FakeLLM())

    output = asyncio.run(planner_module.plan_with_metadata("分析一下"))

    assert output.tasks == []
    assert output.failure_code == "PLANNER_OUTPUT_INVALID"


def test_service_abstain_becomes_blocked_clarification(monkeypatch, tmp_path):
    """Intent wants a file write; Planner abstains → BLOCKED + question output."""
    from tests.test_ha_r1_harness import (
        ScriptedProvider,
        intent,
        run_turn,
        service_at,
    )

    request = "把那个结果写一下"
    provider = ScriptedProvider(
        [
            (
                request,
                intent(request, artifact=True, outcomes=["FILE_MUTATION"]),
            ),
            ("用户需求", {"tasks": [], "abstain_reason": "缺少目标文件"}),
        ]
    )
    monkeypatch.setattr("agent.llm.llm", provider)
    from agent.cognition.intent_engine import IntentEngine

    engine = IntentEngine()
    engine._llm = provider
    monkeypatch.setattr("agent.orchestrator.planner.intent_engine", engine)
    monkeypatch.setattr("agent.planner.planner.llm", provider)
    monkeypatch.setattr("agent.answer_generator.llm", provider)

    async def scenario():
        service, _store = service_at(tmp_path)
        try:
            snapshot, _events = await run_turn(service, request, 1)
            return snapshot
        finally:
            await service.close()

    snapshot = asyncio.run(scenario())
    assert not provider.replies
    assert snapshot.status.value == "BLOCKED", snapshot
    assert "需要你补充信息：缺少目标文件" in snapshot.output.text
    assert "EXECUTION_EVIDENCE_MISSING" not in snapshot.output.text


def test_service_calculator_request_completes(monkeypatch, tmp_path):
    """User's original request goes through Provider and writes the file."""
    from tests.test_ha_r1_harness import (
        ScriptedProvider,
        intent,
        run_turn,
        service_at,
    )

    request = "写个计算器，并告诉我放在哪个目录底下了"
    resolved = "编写一个计算器，完成后告诉用户它被放在哪个目录下。"
    source = "def add(a, b):\n    return a + b\n"
    provider = ScriptedProvider(
        [
            (
                request,
                intent(
                    request,
                    resolved=resolved,
                    artifact=True,
                    outcomes=["FILE_MUTATION"],
                ),
            ),
            ("用户需求", {"tasks": [_task()]}),
            ("计算器", source),
        ]
    )
    monkeypatch.setattr("agent.llm.llm", provider)
    from agent.cognition.intent_engine import IntentEngine

    engine = IntentEngine()
    engine._llm = provider
    monkeypatch.setattr("agent.orchestrator.planner.intent_engine", engine)
    monkeypatch.setattr("agent.planner.planner.llm", provider)
    monkeypatch.setattr("agent.answer_generator.llm", provider)

    async def scenario():
        service, _store = service_at(tmp_path)
        try:
            snapshot, _events = await run_turn(service, request, 1)
            return snapshot
        finally:
            await service.close()

    snapshot = asyncio.run(scenario())
    assert not provider.replies
    assert snapshot.status.value == "COMPLETED", snapshot
    assert (tmp_path / "output" / "calculator.py").read_text().strip() == source.strip()

