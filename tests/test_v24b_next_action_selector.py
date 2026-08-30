from __future__ import annotations

import ast
import asyncio
import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.next_action import ActionKind, NextAction
from agent.next_action_selector import (
    ActionObservation,
    ExecutionStateProjection,
    NextActionSelectionError,
    NextActionSelector,
    TaskProjection,
    selector_state_projection_hash,
)
from agent.tool_action_projection import ToolActionProjection
from evals.tool_selection.oracle import load_dataset


ROOT = Path(__file__).resolve().parents[1]


def _task() -> TaskProjection:
    return TaskProjection(
        id="read-runtime",
        verb="read",
        target="agent/runtime.py",
        target_type="file",
        status="pending",
        dependencies=(),
    )


def _state(*, answer_ready: bool = False) -> ExecutionStateProjection:
    return ExecutionStateProjection(
        goal="读取文件并准备回答",
        current_task_id="read-runtime",
        tasks=(_task(),),
        required_outcomes=("FILE_READ", "USER_VISIBLE_OUTPUT"),
        completed_outcomes=(),
        answer_ready=answer_ready,
        available_actions=(ToolActionProjection(
            tool="filesystem.read",
            args_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
                "additionalProperties": False,
            },
        ),),
        completion_evidence=(),
        history=(),
    )


class _StructuredProvider:
    def __init__(self, action: dict[str, object]) -> None:
        self.action = action
        self.structured_calls = 0
        self.raw_calls = 0
        self.messages = None

    def with_structured_output(self, _schema):
        provider = self

        class Runnable:
            async def ainvoke(self, messages):
                provider.structured_calls += 1
                provider.messages = messages
                return dict(provider.action)

        return Runnable()

    async def ainvoke(self, _messages):
        self.raw_calls += 1
        raise AssertionError("raw path must not run after structured success")


class _FormatFallbackProvider:
    def __init__(self, content: str) -> None:
        self.content = content
        self.structured_calls = 0
        self.raw_calls = 0

    def with_structured_output(self, _schema):
        provider = self

        class Runnable:
            async def ainvoke(self, _messages):
                provider.structured_calls += 1
                raise RuntimeError("response_format unsupported")

        return Runnable()

    async def ainvoke(self, _messages):
        self.raw_calls += 1
        return SimpleNamespace(content=self.content)


def test_production_selector_has_the_frozen_narrow_signature() -> None:
    parameters = inspect.signature(NextActionSelector.select).parameters

    assert tuple(parameters) == ("self", "task", "state", "observation")
    assert inspect.iscoroutinefunction(NextActionSelector.select)


def test_selector_state_v2_contract_hash_is_frozen() -> None:
    assert selector_state_projection_hash() == (
        "ec6ec4f58a567275cd04f9f87cc0723fdd04a64457d2075208da5786ed90d358"
    )


def test_frozen_v1_dataset_remains_bound_to_its_historical_projection() -> None:
    for case in load_dataset()["cases"]:
        assert "available_tools" in case["state"]
        assert "available_actions" not in case["state"]
        with pytest.raises(ValueError):
            ExecutionStateProjection.model_validate(case["state"])


def test_structured_selection_returns_canonical_next_action_without_mutation() -> None:
    provider = _StructuredProvider({
        "kind": "tool",
        "tool": "filesystem.read",
        "args": {"path": "agent/runtime.py"},
        "reason": "read the pending task target",
        "task_id": "read-runtime",
    })
    selector = NextActionSelector(provider=provider, provider_name="deepseek")
    state = _state()
    before = state.model_dump(mode="json")

    selection = asyncio.run(
        selector.select_with_evidence(_task(), state, ActionObservation())
    )

    assert selection.action == NextAction.tool_call(
        "filesystem.read",
        task_id="read-runtime",
        args={"path": "agent/runtime.py"},
        reason="read the pending task target",
    )
    assert selection.evidence.provider == "deepseek"
    assert selection.evidence.provider_path == "SINGLE_PROVIDER"
    assert selection.evidence.format_path == "STRUCTURED_ONLY"
    assert provider.structured_calls == 1
    assert provider.raw_calls == 0
    assert state.model_dump(mode="json") == before


def test_prompt_freezes_mutually_exclusive_canonical_envelopes() -> None:
    provider = _StructuredProvider({
        "kind": "answer",
        "tool": "",
        "args": {},
        "reason": "the Runtime projection marks the answer ready",
        "task_id": "",
    })
    selector = NextActionSelector(provider=provider, provider_name="deepseek")

    selection = asyncio.run(
        selector.select_with_evidence(None, _state(answer_ready=True), None)
    )

    assert selection.action.kind is ActionKind.ANSWER
    assert provider.messages is not None
    system_prompt = provider.messages[0].content
    assert '{"kind":"answer","tool":"","args":{}' in system_prompt
    assert '{"kind":"ask","tool":"","args":{}' in system_prompt
    assert "Do not use null" in system_prompt
    assert "When state.answer_ready is true, choose ANSWER" in system_prompt
    assert provider.structured_calls == 1


def test_structured_to_raw_fallback_is_same_provider_and_observable() -> None:
    provider = _FormatFallbackProvider(json.dumps({
        "kind": "tool",
        "tool": "filesystem.read",
        "args": {"path": "agent/runtime.py"},
        "reason": "use the available read primitive",
        "task_id": "read-runtime",
    }))
    selector = NextActionSelector(provider=provider, provider_name="deepseek")

    selection = asyncio.run(
        selector.select_with_evidence(_task(), _state(), ActionObservation())
    )

    assert selection.action.kind is ActionKind.TOOL
    assert selection.evidence.provider == "deepseek"
    assert selection.evidence.provider_path == "SINGLE_PROVIDER"
    assert selection.evidence.format_path == "STRUCTURED_TO_RAW_FALLBACK"
    assert "response_format unsupported" in selection.evidence.structured_error
    assert provider.structured_calls == 1
    assert provider.raw_calls == 1


def test_raw_only_path_does_not_probe_structured_output() -> None:
    provider = _FormatFallbackProvider(json.dumps({
        "kind": "tool",
        "tool": "filesystem.read",
        "args": {"path": "agent/runtime.py"},
        "reason": "read",
        "task_id": "read-runtime",
    }))
    selector = NextActionSelector(
        provider=provider,
        provider_name="ollama",
        supports_structured_output=False,
    )

    selection = asyncio.run(
        selector.select_with_evidence(_task(), _state(), ActionObservation())
    )

    assert selection.evidence.format_path == "RAW_ONLY"
    assert provider.structured_calls == 0
    assert provider.raw_calls == 1


def test_raw_response_is_not_repaired_or_extracted_from_markdown() -> None:
    provider = _FormatFallbackProvider(
        '```json\n{"kind":"ask","tool":"","args":{},"reason":"need path","task_id":""}\n```'
    )
    selector = NextActionSelector(
        provider=provider,
        provider_name="ollama",
        supports_structured_output=False,
    )

    with pytest.raises(NextActionSelectionError) as captured:
        asyncio.run(selector.select(_task(), _state(), ActionObservation()))

    assert captured.value.code == "SCHEMA_INVALID"
    assert captured.value.provider == "ollama"
    assert captured.value.format_path == "RAW_ONLY"


def test_premature_answer_is_rejected_with_candidate_evidence() -> None:
    provider = _StructuredProvider({
        "kind": "answer",
        "tool": "",
        "args": {},
        "reason": "done",
        "task_id": "",
    })
    selector = NextActionSelector(provider=provider, provider_name="deepseek")

    with pytest.raises(NextActionSelectionError) as captured:
        asyncio.run(selector.select(_task(), _state(), ActionObservation()))

    assert captured.value.code == "PREMATURE_ANSWER"
    assert captured.value.candidate is not None
    assert captured.value.candidate.kind is ActionKind.ANSWER
    assert captured.value.provider == "deepseek"


def test_tool_arguments_must_match_projected_registry_schema() -> None:
    provider = _StructuredProvider({
        "kind": "tool",
        "tool": "filesystem.read",
        "args": {"url": "agent/runtime.py"},
        "reason": "wrong argument name",
        "task_id": "read-runtime",
    })
    selector = NextActionSelector(provider=provider, provider_name="deepseek")

    with pytest.raises(NextActionSelectionError) as captured:
        asyncio.run(selector.select(_task(), _state(), ActionObservation()))

    assert captured.value.code == "ARGUMENT_SCHEMA_INVALID"
    assert captured.value.candidate is not None


def test_selector_source_does_not_cross_frozen_runtime_boundaries() -> None:
    path = ROOT / "agent" / "next_action_selector.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)

    forbidden = {
        "agent.planner",
        "agent.runtime",
        "agent.runtime_store",
        "agent.checkpoint",
        "agent.executor",
        "agent.registry",
        "agent.services.workspace_service",
    }
    assert imports.isdisjoint(forbidden)
