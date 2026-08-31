from __future__ import annotations

import asyncio
import inspect
import json
from types import SimpleNamespace

import pytest

from agent.workflow import Workflow
from agent.workflow_decision import WorkflowDecisionKind
from agent.workflow_selector import (
    ActiveWorkflowProjection,
    WorkflowContextProjection,
    WorkflowDecisionSelector,
    WorkflowDefinitionProjection,
    WorkflowSelectionError,
    workflow_projection_hash,
)


def _workflow(
    *,
    workflow_id: str = "research_report",
    required_artifacts: tuple[str, ...] = (),
) -> WorkflowDefinitionProjection:
    return WorkflowDefinitionProjection(
        id=workflow_id,
        version="1.0",
        description="检索资料并生成报告",
        required_bindings=("topic", "output_path"),
        defaults={},
        required_artifacts=required_artifacts,
        required_capabilities=("web_search", "filesystem.write"),
        output_types=("research_report",),
    )


def _context(
    *,
    active: ActiveWorkflowProjection | None = None,
    artifacts: dict[str, object] | None = None,
) -> WorkflowContextProjection:
    return WorkflowContextProjection(
        artifacts=artifacts or {},
        capabilities=("web_search", "filesystem.write"),
        facts={},
        active_workflow=active,
    )


class _StructuredProvider:
    def __init__(self, decision: dict[str, object]) -> None:
        self.decision = decision
        self.structured_calls = 0
        self.raw_calls = 0
        self.messages = None

    def with_structured_output(self, _schema):
        provider = self

        class Runnable:
            async def ainvoke(self, messages):
                provider.structured_calls += 1
                provider.messages = messages
                return dict(provider.decision)

        return Runnable()

    async def ainvoke(self, _messages):
        self.raw_calls += 1
        raise AssertionError("raw path must not run after structured success")


class _RawProvider:
    def __init__(self, content: str, *, structured_error: bool = False) -> None:
        self.content = content
        self.structured_error = structured_error
        self.structured_calls = 0
        self.raw_calls = 0

    def with_structured_output(self, _schema):
        provider = self

        class Runnable:
            async def ainvoke(self, _messages):
                provider.structured_calls += 1
                if provider.structured_error:
                    raise RuntimeError("response_format unsupported")
                raise AssertionError("structured path was not expected")

        return Runnable()

    async def ainvoke(self, _messages):
        self.raw_calls += 1
        return SimpleNamespace(content=self.content)


def test_selector_has_frozen_narrow_signature() -> None:
    parameters = inspect.signature(WorkflowDecisionSelector.select).parameters

    assert tuple(parameters) == (
        "self", "goal", "context", "available_workflows",
    )
    assert inspect.iscoroutinefunction(WorkflowDecisionSelector.select)


def test_workflow_definition_projects_declared_capability_metadata() -> None:
    workflow = Workflow(
        id="example",
        version="1.0",
        description="example workflow",
        stages=[],
        metadata={
            "capability": {
                "required_bindings": ["input_path"],
                "defaults": {},
                "required_artifacts": [],
                "required_capabilities": ["filesystem.read"],
                "output_types": ["report"],
            },
        },
    )

    projection = WorkflowDefinitionProjection.from_workflow(workflow)

    assert projection.id == "example"
    assert projection.required_bindings == ("input_path",)
    assert projection.required_capabilities == ("filesystem.read",)


def test_workflow_definition_without_capability_metadata_fails_fast() -> None:
    workflow = Workflow(id="legacy", stages=[])

    with pytest.raises(ValueError, match="WORKFLOW_CAPABILITY_METADATA_MISSING"):
        WorkflowDefinitionProjection.from_workflow(workflow)


def test_workflow_projection_hash_is_frozen() -> None:
    assert workflow_projection_hash() == (
        "283c02fca3ef07aab5cac01806b677d2ec24dcb75dccf052b1533601c8c84e14"
    )


def test_structured_selection_returns_canonical_decision_without_mutation() -> None:
    provider = _StructuredProvider({
        "kind": "instantiate",
        "workflow_id": "research_report",
        "bindings": {"topic": "SQLite WAL", "output_path": "output/wal.md"},
        "reason": "the complete goal matches the available report workflow",
    })
    selector = WorkflowDecisionSelector(provider=provider, provider_name="deepseek")
    context = _context()
    before = context.model_dump(mode="json")

    selection = asyncio.run(selector.select_with_evidence(
        "研究 SQLite WAL 并写入 output/wal.md",
        context,
        (_workflow(),),
    ))

    assert selection.decision.kind is WorkflowDecisionKind.INSTANTIATE
    assert selection.decision.workflow_id == "research_report"
    assert selection.evidence.provider_path == "SINGLE_PROVIDER"
    assert selection.evidence.format_path == "STRUCTURED_ONLY"
    assert provider.structured_calls == 1
    assert provider.raw_calls == 0
    assert context.model_dump(mode="json") == before


def test_structured_to_raw_fallback_is_same_provider_and_observable() -> None:
    provider = _RawProvider(json.dumps({
        "kind": "decline",
        "workflow_id": "",
        "bindings": {},
        "reason": "simple task",
    }), structured_error=True)
    selector = WorkflowDecisionSelector(provider=provider, provider_name="deepseek")

    selection = asyncio.run(selector.select_with_evidence(
        "读取 README.md",
        _context(),
        (_workflow(),),
    ))

    assert selection.decision.kind is WorkflowDecisionKind.DECLINE
    assert selection.evidence.format_path == "STRUCTURED_TO_RAW_FALLBACK"
    assert "response_format unsupported" in selection.evidence.structured_error
    assert provider.structured_calls == 1
    assert provider.raw_calls == 1


def test_raw_response_is_not_repaired_or_extracted_from_markdown() -> None:
    provider = _RawProvider(
        '```json\n{"kind":"decline","workflow_id":"","bindings":{},"reason":"simple"}\n```'
    )
    selector = WorkflowDecisionSelector(
        provider=provider,
        provider_name="ollama",
        supports_structured_output=False,
    )

    with pytest.raises(WorkflowSelectionError) as captured:
        asyncio.run(selector.select("读取 README.md", _context(), (_workflow(),)))

    assert captured.value.code == "SCHEMA_INVALID"
    assert captured.value.provider == "ollama"
    assert captured.value.format_path == "RAW_ONLY"


@pytest.mark.parametrize(
    ("decision", "code"),
    [
        ({
            "kind": "instantiate",
            "workflow_id": "unknown",
            "bindings": {"topic": "x", "output_path": "output/x.md"},
            "reason": "match",
        }, "UNAVAILABLE_WORKFLOW"),
        ({
            "kind": "instantiate",
            "workflow_id": "research_report",
            "bindings": {"topic": "x"},
            "reason": "match",
        }, "BINDINGS_INVALID"),
    ],
)
def test_invalid_instantiation_is_rejected_before_execution(
    decision: dict[str, object],
    code: str,
) -> None:
    selector = WorkflowDecisionSelector(
        provider=_StructuredProvider(decision),
        provider_name="deepseek",
    )

    with pytest.raises(WorkflowSelectionError) as captured:
        asyncio.run(selector.select("goal", _context(), (_workflow(),)))

    assert captured.value.code == code
    assert captured.value.candidate is not None


def test_missing_projected_capability_is_rejected() -> None:
    provider = _StructuredProvider({
        "kind": "instantiate",
        "workflow_id": "research_report",
        "bindings": {"topic": "x", "output_path": "output/x.md"},
        "reason": "match",
    })
    selector = WorkflowDecisionSelector(provider=provider, provider_name="deepseek")
    context = WorkflowContextProjection(capabilities=("filesystem.write",))

    with pytest.raises(WorkflowSelectionError) as captured:
        asyncio.run(selector.select("goal", context, (_workflow(),)))

    assert captured.value.code == "REQUIRED_CAPABILITY_UNAVAILABLE"


def test_missing_projected_artifact_is_rejected() -> None:
    provider = _StructuredProvider({
        "kind": "instantiate",
        "workflow_id": "research_report",
        "bindings": {"topic": "x", "output_path": "output/x.md"},
        "reason": "match",
    })
    selector = WorkflowDecisionSelector(provider=provider, provider_name="deepseek")

    with pytest.raises(WorkflowSelectionError) as captured:
        asyncio.run(selector.select(
            "goal",
            _context(),
            (_workflow(required_artifacts=("brief",)),),
        ))

    assert captured.value.code == "REQUIRED_ARTIFACT_UNAVAILABLE"


def test_reuse_requires_active_runtime_authorization() -> None:
    provider = _StructuredProvider({
        "kind": "reuse",
        "workflow_id": "research_report",
        "bindings": {},
        "reason": "continue",
    })
    selector = WorkflowDecisionSelector(provider=provider, provider_name="deepseek")
    context = _context(active=ActiveWorkflowProjection(
        workflow_id="research_report",
        status="blocked",
        reuse_allowed=False,
    ))

    with pytest.raises(WorkflowSelectionError) as captured:
        asyncio.run(selector.select("继续", context, (_workflow(),)))

    assert captured.value.code == "UNSAFE_REUSE"


def test_prompt_preserves_workflow_resume_and_execution_boundaries() -> None:
    provider = _StructuredProvider({
        "kind": "ask",
        "workflow_id": "",
        "bindings": {},
        "reason": "missing output path",
    })
    selector = WorkflowDecisionSelector(provider=provider, provider_name="deepseek")

    decision = asyncio.run(selector.select(
        "生成研究报告",
        _context(),
        (_workflow(),),
    ))

    assert decision.kind is WorkflowDecisionKind.ASK
    prompt = provider.messages[0].content
    assert "Do not plan tasks" in prompt
    assert "execute or resume a Workflow" in prompt
    assert "create retry policy" in prompt
