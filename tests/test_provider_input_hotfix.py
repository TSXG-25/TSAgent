"""Deterministic regressions for provider outage and invalid-input handling."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from agent.execution_errors import classify_execution_error
from agent.cognition.cognitive_context import CognitiveContext
from agent.cognition.intent_engine import IntentEngine
from agent.llm import LLMRouter
from agent.orchestrator.planner import PlannerStage, _apply_planner_failure
from agent.planner import planner as planner_module
from agent.planner.planner import PlanOutput
from agent.runtime import UniversalAgent
from agent.runtime_gates import is_empty_or_punctuation_request
from agent.service import (
    AgentService,
    EventStreamRequest,
    EventType,
    RunLookupRequest,
    RunStatus,
    StartRunRequest,
)
from agent.state import AgentState
from agent.runtime_store import SqliteRuntimeStore
from agent.service.runtime_launcher import RuntimeExecutionLauncher


def test_provider_unavailable_becomes_plan_failure_not_fake_task(monkeypatch) -> None:
    class FailingLLM:
        supports_structured_output = False

        async def ainvoke(self, _messages: Any) -> Any:
            raise RuntimeError(
                "所有 LLM 提供商均不可用。最后一次错误: Connection error."
            )

    monkeypatch.setattr(planner_module, "llm", FailingLLM())
    output = asyncio.run(
        planner_module.plan_with_metadata("分析一个问题", "", "", "", None)
    )

    assert output.tasks == []
    assert output.failure_code == "PROVIDER_UNAVAILABLE"
    assert classify_execution_error(output.failure_message) == ""


def test_intent_provider_failure_is_explicit_and_not_unknown_fallback() -> None:
    class FailingLLM:
        def invoke(self, _messages: Any) -> Any:
            raise RuntimeError(
                "所有 LLM 提供商均不可用。最后一次错误: Connection error."
            )

    engine = IntentEngine()
    engine._llm = FailingLLM()
    intent = engine.analyze(CognitiveContext(query="请处理这个没有关键词的请求"))

    assert intent.domain == "unknown"
    assert intent.failure_code == "PROVIDER_UNAVAILABLE"
    assert intent.requires_execution is True


def test_intent_provider_failure_short_circuits_generic_planner(monkeypatch) -> None:
    from agent.cognition.intent_schema import IntentResult
    from agent.orchestrator import planner as orchestrator_planner_module

    monkeypatch.setattr(
        orchestrator_planner_module.intent_engine,
        "analyze",
        lambda _context: IntentResult(
            domain="unknown",
            requires_execution=True,
            failure_code="PROVIDER_UNAVAILABLE",
            failure_message="当前 LLM 服务暂时不可用，本次未生成或执行任务。",
        ),
    )

    async def forbidden_plan(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("provider outage must not enter generic Planner")

    monkeypatch.setattr(
        orchestrator_planner_module,
        "plan_with_metadata",
        forbidden_plan,
    )
    context_builder = SimpleNamespace(
        render_context=lambda _context, _now: "",
        build=lambda **_kwargs: SimpleNamespace(short_summary=lambda: ""),
    )
    resolver = SimpleNamespace(
        resolve=lambda _input, _context: SimpleNamespace(
            target="",
            symbol="",
            resolution_trace="",
            to_resolved_query=lambda: "",
        )
    )
    orchestrator = SimpleNamespace(
        _context_builder=context_builder,
        _reference_resolver=resolver,
        session_context=SimpleNamespace(memory_view=None),
        _timings={},
    )

    async def scenario() -> None:
        state, next_state, answer = await PlannerStage(orchestrator).run(
            "请处理这个没有关键词的请求",
            "user-a",
            {},
            "",
            "",
        )
        assert next_state == "FAIL"
        assert state["runtime_failure_code"] == "PROVIDER_UNAVAILABLE"
        assert answer == "当前 LLM 服务暂时不可用，本次未生成或执行任务。"

    asyncio.run(scenario())


def test_planner_failure_is_terminal_and_does_not_enter_replan() -> None:
    state: AgentState = {}
    result = _apply_planner_failure(
        state,
        PlanOutput(
            failure_code="PROVIDER_UNAVAILABLE",
            failure_message="当前 LLM 服务暂时不可用，本次未生成或执行任务。",
        ),
    )

    assert result == (
        state,
        "FAIL",
        "当前 LLM 服务暂时不可用，本次未生成或执行任务。",
    )
    assert state["runtime_terminal_status"] == "FAILED_TERMINAL"
    assert state["runtime_failure_code"] == "PROVIDER_UNAVAILABLE"


def test_provider_unavailable_task_is_non_retriable() -> None:
    stage = PlannerStage(SimpleNamespace(replan_count=0))
    state: AgentState = {
        "plan": [{
            "id": "task-1",
            "status": "failed",
            "error": "所有 LLM 提供商均不可用",
            "error_code": "PROVIDER_UNAVAILABLE",
        }],
    }

    updated, next_state = asyncio.run(
        stage.replan(state, "分析一个问题", "user-a")
    )

    assert next_state == "FAIL"
    assert updated["runtime_failure_code"] == "PROVIDER_UNAVAILABLE"
    assert stage._orch.replan_count == 0


def test_llm_router_does_not_retry_same_provider_in_one_request() -> None:
    calls: list[str] = []

    class FailingProvider:
        def __init__(self, name: str) -> None:
            self.name = name

        async def ainvoke(self, _messages: Any, **_kwargs: Any) -> Any:
            calls.append(self.name)
            raise ConnectionError(f"{self.name} unavailable")

    router = LLMRouter()
    router._deepseek = FailingProvider("deepseek")  # type: ignore[assignment]
    router._ollama = FailingProvider("ollama")  # type: ignore[assignment]

    try:
        asyncio.run(router.ainvoke([]))
    except RuntimeError as error:
        assert "所有 LLM 提供商均不可用" in str(error)
    else:
        raise AssertionError("provider outage must fail deterministically")

    assert calls == ["deepseek", "ollama"]


def test_punctuation_only_input_skips_runtime_planner() -> None:
    assert is_empty_or_punctuation_request("？")
    assert is_empty_or_punctuation_request(" ... ")
    assert not is_empty_or_punctuation_request("2+2")

    called: list[str] = []

    class GateAgent(UniversalAgent):
        def _ensure_run_subscription(self):
            return None

        async def _run_in_context(self, _user_input: str) -> str:
            called.append("planner")
            raise AssertionError("punctuation input must not enter Planner")

    agent = GateAgent.__new__(GateAgent)
    agent._run_context = None
    agent._memory_view = SimpleNamespace(
        record_full_exchange=lambda *_args: None,
    )
    answer = asyncio.run(agent.run("？"))

    assert "仅包含标点" in answer
    assert called == []
    assert agent.last_run_evidence["failure_code"] == "INVALID_REQUEST"
    assert agent.last_run_evidence["terminal_status"] == "BLOCKED"


def test_service_provider_outage_has_one_terminal_failure_without_replan(
    tmp_path,
) -> None:
    class ProviderUnavailableRuntime:
        calls = 0

        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            self.last_run_evidence = {
                "terminal_status": "FAILED_TERMINAL",
                "terminal_outputs_verified": False,
                "runtime_pending": False,
                "task_failures": [],
                "failure_code": "PROVIDER_UNAVAILABLE",
                "failure_class": "provider",
                "failed_component": "planner_llm",
                "retryable": True,
                "answer": "当前 LLM 服务暂时不可用，本次未生成或执行任务。",
                "answer_required": True,
                "user_visible_output_verified": True,
                "fresh_evidence": True,
            }

        async def run(self, _request_text: str) -> str:
            type(self).calls += 1
            return str(self.last_run_evidence["answer"])

        def close(self) -> None:
            return None

    async def scenario() -> None:
        ProviderUnavailableRuntime.calls = 0
        store = SqliteRuntimeStore.open(tmp_path / "provider-outage.sqlite")
        service = AgentService(
            runtime_store=store,
            launcher=RuntimeExecutionLauncher(
                runtime_factory=ProviderUnavailableRuntime,
            ),
        )
        try:
            handle = await service.start_run(StartRunRequest(
                tenant_id="tenant-a",
                user_id="user-a",
                session_id="session-a",
                run_id="provider-outage-run",
                request_id="provider-outage-request",
                request_text="分析一个问题",
            ))
            events = [event async for event in service.stream_events(
                EventStreamRequest(
                    tenant_id="tenant-a",
                    user_id="user-a",
                    session_id="session-a",
                    run_id=handle.run_id,
                    request_id="provider-outage-events",
                    after_sequence=0,
                )
            )]
            snapshot = await service.get_run(RunLookupRequest(
                tenant_id="tenant-a",
                user_id="user-a",
                session_id="session-a",
                run_id=handle.run_id,
                request_id="provider-outage-snapshot",
            ))
        finally:
            await service.close()
            if not store.closed:
                store.close()

        assert [event.event_type for event in events] == [
            EventType.RUN_CREATED,
            EventType.RUN_STARTED,
            EventType.RUN_FAILED,
        ]
        assert snapshot.status is RunStatus.FAILED_TERMINAL
        assert snapshot.failure_summary is not None
        assert snapshot.failure_summary.code == "PROVIDER_UNAVAILABLE"
        assert EventType.RUN_COMPLETED not in [event.event_type for event in events]
        assert ProviderUnavailableRuntime.calls == 1

    asyncio.run(scenario())
