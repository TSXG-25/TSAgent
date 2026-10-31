"""Deterministic v2.3H3 freshness and user-visible output contracts."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from agent.runtime import UniversalAgent, _build_run_evidence
from agent.runtime_context import ApplicationContext
from agent.runtime_store import DurableRuntimeStoreView, SqliteRuntimeStore
from agent.service.runtime_launcher import RuntimeExecutionLauncher
from agent.state import AgentState
from agent.service import (
    AgentService,
    EventStreamRequest,
    RunLookupRequest,
    StartRunRequest,
)


def _state(**values: Any) -> AgentState:
    return cast(AgentState, {
        "plan": [],
        "freshness_required": False,
        "source_grounding_required": False,
        "fresh_evidence": False,
        "answer_required": True,
        "runtime_terminal_status": "COMPLETED",
        **values,
    })


def _seed_terminal_run(
    store: SqliteRuntimeStore,
    *,
    tenant_id: str,
    session_id: str,
    run_id: str,
    status: str,
    output: str = "",
    failure_code: str = "",
) -> None:
    store.initialize_run(tenant_id, session_id, run_id, run_status="RUNNING")
    writer_id = f"seed-writer-{run_id}"
    view = DurableRuntimeStoreView(
        store,
        tenant_id=tenant_id,
        session_id=session_id,
        run_id=run_id,
        request_id=f"seed-request-{run_id}",
        writer_id=writer_id,
        ensure_run=False,
    )
    try:
        view.transition_run_with_event(
            run_status=status,
            event_id=f"seed-event-{run_id}",
            event_type={
                "COMPLETED": "run_completed",
                "FAILED_TERMINAL": "run_failed",
                "BLOCKED": "run_blocked",
            }[status],
            timestamp="2026-08-10T00:00:00Z",
            payload=(
                {"failure_code": failure_code}
                if failure_code
                else {"request_id": f"seed-request-{run_id}"}
            ),
            run_output=(
                {"text": output, "evidence_ids": [], "artifact_ids": []}
                if output
                else None
            ),
        )
    finally:
        view.close()


def _output_request_agent(
    store: SqliteRuntimeStore,
    *,
    tenant_id: str,
    session_id: str,
    run_id: str = "current-run",
) -> UniversalAgent:
    app = ApplicationContext(runtime_store=store)
    session = app.create_session(
        session_id,
        user_id="user-a",
        tenant_id=tenant_id,
    )
    run = session.create_run(run_id, request_id=f"request-{run_id}")
    agent = UniversalAgent(
        "user-a",
        tenant_id=tenant_id,
        session_context=session,
        run_context=run,
    )
    # Keep the ApplicationContext alive through the agent's lifetime. The
    # test closes it explicitly after the agent releases the Run view.
    agent._h3_test_application = app  # type: ignore[attr-defined]
    return agent


def _close_output_request_agent(agent: UniversalAgent) -> None:
    app = getattr(agent, "_h3_test_application")
    agent.close()
    app.close()


def test_h301_h302_h303_freshness_gate() -> None:

    missing = _build_run_evidence(
        _state(freshness_required=True, source_grounding_required=True),
        "模型记忆里的市场情况",
        1,
        "分析下今天A股市场",
    )
    assert missing["terminal_status"] == "BLOCKED"
    assert missing["failure_code"] == "RESEARCH_TOOL_UNAVAILABLE"
    assert missing["terminal_outputs_verified"] is False

    grounded = _build_run_evidence(
        _state(
            freshness_required=True,
            source_grounding_required=True,
            plan=[{
                "status": "succeeded",
                "observations": [{
                    "status": "succeeded",
                    "tools": ["web_search"],
                    "summary": "source-backed result",
                }],
            }],
        ),
        "基于来源的市场摘要",
        2,
        "分析下今天A股市场",
    )
    assert grounded["terminal_status"] == "COMPLETED"
    assert grounded["fresh_evidence"] is True
    assert grounded["terminal_outputs_verified"] is True

    chat = _build_run_evidence(
        _state(),
        "A股是中国内地股票市场的简称。",
        1,
        "解释什么是A股",
    )
    assert chat["terminal_status"] == "COMPLETED"


def test_h304_answer_required_run_without_output_cannot_complete() -> None:
    evidence = _build_run_evidence(
        _state(),
        "",
        1,
        "请给出最终结果",
    )
    assert evidence["terminal_status"] == "FAILED_TERMINAL"
    assert evidence["failure_code"] == "MISSING_USER_OUTPUT"
    outcome = RuntimeExecutionLauncher._terminal_outcome(
        SimpleNamespace(last_run_evidence=evidence)
    )
    assert outcome == ("FAILED_TERMINAL", "run_failed", "MISSING_USER_OUTPUT")


def test_direct_chat_provider_failure_cannot_emit_completed(monkeypatch) -> None:
    class FailingLLM:
        async def ainvoke(self, _messages):
            raise TimeoutError("provider unavailable")

    monkeypatch.setattr("agent.llm.llm", FailingLLM())
    from agent.cognition.intent_engine import IntentEngine
    from agent.cognition.cognitive_context import CognitiveContext

    engine = IntentEngine()
    engine._llm = FailingLLM()
    result = asyncio.run(engine.analyze_async(CognitiveContext(query="你好")))

    assert result.failure_code.startswith("PROVIDER_")
    assert result.failure_message



def test_run_output_is_persisted_and_projected_after_service_restart(tmp_path: Path) -> None:
    class AnswerRuntime:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.last_run_evidence: dict[str, Any] = {}

        async def run(self, request_text: str) -> str:
            self.last_run_evidence = {
                "terminal_status": "COMPLETED",
                "terminal_outputs_verified": True,
                "runtime_pending": False,
                "task_failures": [],
                "answer": "durable user-visible output",
                "answer_required": True,
                "user_visible_output_verified": True,
                "freshness_required": False,
                "source_grounding_required": False,
                "fresh_evidence": True,
            }
            return "durable user-visible output"

        def close(self) -> None:
            return None

    async def scenario() -> None:
        database = tmp_path / "service-output.sqlite"
        store = SqliteRuntimeStore.open(database)
        service = AgentService(
            runtime_store=store,
            launcher=RuntimeExecutionLauncher(runtime_factory=AnswerRuntime),
        )
        request = StartRunRequest(
            tenant_id="tenant-a",
            user_id="user-a",
            session_id="session-a",
            run_id="service-output-run",
            request_id="output-start",
            request_text="生成一个结果",
        )
        try:
            handle = await service.start_run(request)
            events = service.stream_events(
                EventStreamRequest(
                    tenant_id="tenant-a",
                    user_id="user-a",
                    session_id="session-a",
                    run_id=handle.run_id,
                    request_id="output-events",
                    after_sequence=0,
                )
            )
            [event async for event in events]
            snapshot = await service.get_run(
                RunLookupRequest(
                    tenant_id="tenant-a",
                    user_id="user-a",
                    session_id="session-a",
                    run_id=handle.run_id,
                    request_id="output-snapshot",
                )
            )
            assert snapshot.output is not None
            assert snapshot.output.text == "durable user-visible output"
            assert snapshot.status.value == "COMPLETED"
        finally:
            await service.close()
            if not store.closed:
                store.close()

        reopened = SqliteRuntimeStore.open(database)
        try:
            # The durable read contract, not the old Service process, is the
            # source of truth after a restart.
            read = reopened.read_run_snapshot(
                "tenant-a",
                handle.run_id,
                session_id="session-a",
            )
            assert read.output is not None
            assert read.output.text == "durable user-visible output"
        finally:
            reopened.close()

    asyncio.run(scenario())

