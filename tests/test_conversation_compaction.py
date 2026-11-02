"""Durable cross-turn compaction: summary persists in SQLite and history
beyond the cutoff is still fetched verbatim."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from agent.runtime_store import SqliteRuntimeStore


def _store(tmp_path) -> SqliteRuntimeStore:
    return SqliteRuntimeStore.open(tmp_path / "runtime.sqlite")


def test_session_summary_round_trip(tmp_path):
    store = _store(tmp_path)
    store.write_session_summary(
        "tenant-a",
        "session-a",
        through_updated_at="2026-01-01T00:00:00Z",
        through_run_id="run-2",
        summary="前两轮要点",
    )
    record = store.read_session_summary("tenant-a", "session-a")
    assert record == {
        "through_updated_at": "2026-01-01T00:00:00Z",
        "through_run_id": "run-2",
        "summary": "前两轮要点",
    }
    # Upsert replaces the previous summary.
    store.write_session_summary(
        "tenant-a",
        "session-a",
        through_updated_at="2026-01-02T00:00:00Z",
        through_run_id="run-5",
        summary="更新后的摘要",
    )
    record = store.read_session_summary("tenant-a", "session-a")
    assert record["summary"] == "更新后的摘要"
    assert record["through_run_id"] == "run-5"
    store.close()


def test_recent_terminal_run_ids_respects_summary_cutoff(tmp_path):
    store = _store(tmp_path)
    for index in range(5):
        store.initialize_run("tenant-a", "session-a", f"run-{index}", "req")
    # Force terminal statuses and deterministic timestamps.
    for index, status in enumerate(
        ["COMPLETED", "COMPLETED", "BLOCKED", "COMPLETED", "RUNNING"]
    ):
        store._connection.execute(
            "UPDATE run_heads SET run_status = ?, updated_at = ? "
            "WHERE tenant_id = ? AND run_id = ?",
            (status, f"2026-01-0{index + 1}T00:00:00Z", "tenant-a", f"run-{index}"),
        )
    store._connection.commit()

    all_ids = store.recent_terminal_run_ids(
        "tenant-a", "session-a", exclude_run_id="run-x"
    )
    assert all_ids == ("run-0", "run-1", "run-2", "run-3")

    filtered = store.recent_terminal_run_ids(
        "tenant-a",
        "session-a",
        exclude_run_id="run-x",
        after_updated_at="2026-01-02T00:00:00Z",
        after_run_id="run-1",
    )
    assert filtered == ("run-2", "run-3")
    store.close()


def _fake_view(run_results, summary_record=None):
    saved: dict[str, str] = {}

    view = SimpleNamespace(
        session_summary=lambda: summary_record,
        conversation_results=lambda: list(run_results),
        save_session_summary=lambda summary, **kwargs: saved.update(
            {"summary": summary, **{k: str(v) for k, v in kwargs.items()}}
        ),
    )
    return view, saved


def _long_run(run_id: str, updated_at: str, request: str, answer: str):
    return {
        "run_id": run_id,
        "updated_at": updated_at,
        "status": "COMPLETED",
        "request": request,
        "goal": "",
        "goal_contract": {},
        "missing": [],
        "observations": [],
        "acceptance_evidence": [],
        "answer": answer,
        "artifacts": [],
    }


def test_resolve_conversation_under_budget_no_llm_call(monkeypatch):
    from agent.orchestrator.planner import PlannerStage

    runs = [_long_run("run-1", "2026-01-01T00:00:00Z", "q", "a")]
    view, saved = _fake_view(runs)
    stage = PlannerStage(SimpleNamespace())

    called = []
    monkeypatch.setattr(
        "agent.llm.llm.ainvoke",
        lambda *a, **k: called.append(1) or None,
    )
    run_results, conversation = asyncio.run(
        stage._resolve_conversation(SimpleNamespace(durable_store_view=view))
    )
    assert not called
    assert not saved
    assert run_results == runs
    assert conversation[0] == {"role": "user", "content": "q"}


def test_resolve_conversation_compacts_and_persists(monkeypatch):
    from agent.orchestrator.planner import PlannerStage

    runs = [
        _long_run(
            f"run-{i}", f"2026-01-0{i + 1}T00:00:00Z", f"q{i}", "答" * 2500
        )
        for i in range(10)
    ]
    view, saved = _fake_view(runs)
    stage = PlannerStage(SimpleNamespace())

    async def fake_ainvoke(messages, **kwargs):
        return SimpleNamespace(content="压缩后的摘要")

    monkeypatch.setattr("agent.llm.llm.ainvoke", fake_ainvoke)
    run_results, conversation = asyncio.run(
        stage._resolve_conversation(SimpleNamespace(durable_store_view=view))
    )
    assert saved["summary"] == "压缩后的摘要"
    assert saved["through_run_id"] == "run-2"
    assert saved["through_updated_at"] == "2026-01-03T00:00:00Z"
    assert "[早前对话摘要] 压缩后的摘要" in conversation[0]["content"]
    # The compacted prefix is not rendered verbatim.
    assert all(message["content"] != "q0" for message in conversation)
    assert any(message["content"] == "q9" for message in conversation)


def test_resolve_conversation_uses_existing_summary(monkeypatch):
    from agent.orchestrator.planner import PlannerStage

    runs = [_long_run("run-9", "2026-01-09T00:00:00Z", "新问", "新答")]
    view, saved = _fake_view(
        runs,
        summary_record={
            "summary": "旧摘要",
            "through_run_id": "run-8",
            "through_updated_at": "2026-01-08T00:00:00Z",
        },
    )
    stage = PlannerStage(SimpleNamespace())
    monkeypatch.setattr(
        "agent.llm.llm.ainvoke",
        lambda *a, **k: pytest.fail("no LLM call under budget"),
    )
    _, conversation = asyncio.run(
        stage._resolve_conversation(SimpleNamespace(durable_store_view=view))
    )
    assert "[早前对话摘要] 旧摘要" in conversation[0]["content"]
    assert conversation[2] == {"role": "user", "content": "新问"}
