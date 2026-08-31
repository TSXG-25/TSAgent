from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.cognition.intent_schema import DOMAIN_DEVELOPMENT, IntentResult
from agent.conversation import ConversationRetriever, ConversationTracker
from agent.next_action_selector import NextActionSelector
from agent.orchestrator.main import ExecutionOrchestrator
from agent.workflow_decision import WorkflowDecision, WorkflowDecisionKind
from agent.workflow_selector import (
    WorkflowDecisionSelector,
    WorkflowDefinitionProjection,
)
from workflows.code_generation import code_generation_workflow


ROOT = Path(__file__).resolve().parents[1]


def test_code_generation_declares_generic_workflow_capability_metadata() -> None:
    projection = WorkflowDefinitionProjection.from_workflow(code_generation_workflow)

    assert projection.id == "code_generation"
    assert projection.required_bindings == ("question_path", "output_path")
    assert projection.defaults == {"output_path": "output/solution.py"}
    assert projection.required_capabilities == (
        "filesystem.read",
        "filesystem.write",
        "run_python",
    )


def test_orchestrator_composes_both_frozen_decision_boundaries() -> None:
    next_action_selector = NextActionSelector(provider=object())
    workflow_selector = WorkflowDecisionSelector(provider=object())

    orchestrator = ExecutionOrchestrator(
        next_action_selector=next_action_selector,
        workflow_selector=workflow_selector,
    )

    assert orchestrator._next_action_selector is next_action_selector
    assert orchestrator._workflow_selector is workflow_selector


def test_workflow_selector_is_not_owned_by_workflow_executor() -> None:
    source = (
        ROOT / "agent" / "executor" / "executors" / "workflow.py"
    ).read_text(encoding="utf-8")

    assert "WorkflowDecisionSelector" not in source
    assert "WorkflowDecisionKind" not in source


def test_planner_consumes_generic_decision_bindings_without_path_literals() -> None:
    source = (ROOT / "agent" / "orchestrator" / "planner.py").read_text(
        encoding="utf-8"
    )

    assert "_workflow_selector.select_with_evidence(" in source
    assert "for binding_name, binding_value in workflow_decision.bindings.items()" in source
    assert 'type="question_path"' not in source
    assert 'id="output-path"' not in source
    assert "_extract_workflow_output_path(user_input)" not in source


def test_workflow_selector_constructor_is_optional_injection_only() -> None:
    parameters = inspect.signature(ExecutionOrchestrator.__init__).parameters

    assert "workflow_selector" in parameters
    assert parameters["workflow_selector"].default is None


class _MemoryView:
    def __init__(self) -> None:
        self.exchanges: list[tuple[str, str]] = []

    def record_full_exchange(self, user_input: str, answer: str) -> None:
        self.exchanges.append((user_input, answer))


class _WorkflowSelector:
    def __init__(self, decision: WorkflowDecision) -> None:
        self.decision = decision
        self.calls = 0
        self.available = None

    async def select_with_evidence(self, _goal, _context, available):
        self.calls += 1
        self.available = available
        return SimpleNamespace(decision=self.decision)


def test_question_workflow_uses_selected_generic_bindings_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agent.orchestrator.planner as planner_module
    from agent.executor.executors.workflow import WorkflowExecutor

    user_input = "根据 input/question.docx 解题并保存为 output/answer.py"
    workflow_selector = _WorkflowSelector(WorkflowDecision(
        kind=WorkflowDecisionKind.INSTANTIATE,
        workflow_id="code_generation",
        bindings={
            "question_path": "input/question.docx",
            "output_path": "output/answer.py",
        },
    ))
    memory_view = _MemoryView()
    session_context = SimpleNamespace(
        memory_view=memory_view,
        conversation_retriever=ConversationRetriever(ConversationTracker()),
    )
    orchestrator = ExecutionOrchestrator(
        session_context=session_context,
        workflow_selector=workflow_selector,
    )

    planner_context = SimpleNamespace(
        resolved_query="",
        runtime_pending_target="",
        short_summary=lambda: "test context",
    )
    resolution = SimpleNamespace(
        target="",
        symbol="",
        resolution_trace="",
        kind="none",
        to_resolved_query=lambda: "",
    )
    orchestrator._context_builder.render_context = lambda _context, _now: ""
    orchestrator._context_builder.build = lambda **_kwargs: planner_context
    orchestrator._context_builder.update_conversation_state = lambda *_args, **_kwargs: None
    orchestrator._reference_resolver.resolve = lambda *_args: resolution

    async def analyze_async(_context):
        return IntentResult(
            domain=DOMAIN_DEVELOPMENT,
            action="code",
            confidence=1.0,
            requires_execution=True,
            raw_input=user_input,
        )

    monkeypatch.setattr(planner_module.intent_engine, "analyze_async", analyze_async)
    monkeypatch.setattr(planner_module.skill_registry, "select", lambda _input: None)
    monkeypatch.setattr(
        planner_module,
        "_tool_registry",
        SimpleNamespace(get=lambda _name: object(), get_all=lambda: {}),
    )

    execution_contexts = []

    async def execute(_self, _workflow, context):
        execution_contexts.append(context)
        return SimpleNamespace(
            success=True,
            outputs={"_summary": "workflow result"},
            error="",
        )

    monkeypatch.setattr(WorkflowExecutor, "execute", execute)

    state, directive, answer = asyncio.run(orchestrator.plan(
        user_input,
        "user-1",
        {"session": "", "short_term": ""},
        "",
        "",
    ))

    assert directive == "FINISH"
    assert answer == "workflow result"
    assert state["workflow"] == "code_generation"
    assert workflow_selector.calls == 1
    assert workflow_selector.available[0].id == "code_generation"
    assert len(execution_contexts) == 1
    context = execution_contexts[0]
    assert context.get_artifact("question_path").content == "input/question.docx"
    assert context.get_artifact("output_path").content == "output/answer.py"
    assert memory_view.exchanges == [(user_input, "workflow result")]
