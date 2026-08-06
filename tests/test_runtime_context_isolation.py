"""Deterministic v2.3A Runtime Context ownership tests."""

import asyncio
import threading
import pytest

from agent.event_bus import EventBus, EventScopeClosedError
from agent.runtime_context import (
    ApplicationContext,
    ContextClosedError,
)
from agent.services.artifact_service import (
    ArtifactScopeClosedError,
)
from agent.workspace.manager import WorkspaceManager


def test_run_artifacts_are_isolated_even_when_keys_match() -> None:
    app = ApplicationContext()
    session = app.create_session("session-a", user_id="user-a")
    run_a = session.create_run("run-a")
    run_b = session.create_run("run-b")

    run_a.artifacts.put("text", summary="A", key="shared")
    run_b.artifacts.put("text", summary="B", key="shared")

    assert run_a.artifacts.get_summary("shared") == "[text] A"
    assert run_b.artifacts.get_summary("shared") == "[text] B"
    assert run_a.artifacts.get("shared").metadata["scope_id"] == "run:default:session-a:run-a"
    assert run_b.artifacts.get("shared").metadata["scope_id"] == "run:default:session-a:run-b"
    assert run_a.artifacts.get_summary("missing") == ""

    app.close()


def test_event_delivery_is_limited_to_run_scope() -> None:
    app = ApplicationContext()
    session = app.create_session("session-a")
    run_a = session.create_run("run-a")
    run_b = session.create_run("run-b")
    received_a: list[object] = []
    received_b: list[object] = []

    run_a.event_bus.subscribe("task_end", received_a.append)
    run_b.event_bus.subscribe("task_end", received_b.append)
    run_a.event_bus.emit("task_end", {"run_id": "run-a"})

    assert len(received_a) == 1
    assert received_b == []
    app.close()


def test_session_memory_namespaces_do_not_follow_shared_user_id() -> None:
    from agent.memory.session import clear_session, get_session_context
    from agent.services.memory_service import MemoryService

    app = ApplicationContext()
    session_a = app.create_session("session-a", user_id="same-user")
    session_b = app.create_session("session-b", user_id="same-user")
    MemoryService.record_user_message(session_a.memory_namespace, "A-only")
    MemoryService.record_user_message(session_b.memory_namespace, "B-only")

    context_a = get_session_context(session_a.memory_namespace)
    context_b = get_session_context(session_b.memory_namespace)
    assert "A-only" in context_a and "B-only" not in context_a
    assert "B-only" in context_b and "A-only" not in context_b

    clear_session(session_a.memory_namespace)
    clear_session(session_b.memory_namespace)
    app.close()


def test_subscription_close_is_idempotent_and_reset_safe() -> None:
    bus = EventBus(scope_id="test")
    for _ in range(1000):
        subscription = bus.subscribe("event", lambda _: None)
        assert bus.subscriber_count() == 1
        subscription.close()
        subscription.close()
        assert bus.subscriber_count() == 0

    bus.close()
    bus.close()
    assert bus.subscriber_count() == 0


def test_closed_run_rejects_events_and_artifacts() -> None:
    app = ApplicationContext()
    session = app.create_session("session-a")
    run = session.create_run("run-a")
    run.close()

    with pytest.raises(EventScopeClosedError):
        run.event_bus.emit("event", None)
    with pytest.raises(ArtifactScopeClosedError):
        run.artifacts.put("text", summary="late")

    app.close()


def test_run_close_does_not_purge_until_explicit_destroy() -> None:
    app = ApplicationContext()
    session = app.create_session("session-a")
    run = session.create_run("run-a")
    run.artifacts.put("text", summary="recoverable", key="result")

    run.close()
    assert "result" in run.artifacts._store

    run.destroy()
    assert run.artifacts._store == {}
    app.close()


def test_agent_messages_reuse_one_logical_run_until_detached(monkeypatch) -> None:
    from agent.runtime import UniversalAgent

    app = ApplicationContext()
    session = app.create_session("session-a")
    agent = UniversalAgent("user-a", session_context=session)
    answers: list[str] = []

    async def fake_run_in_context(user_input: str) -> str:
        answers.append(user_input)
        return "ok"

    monkeypatch.setattr(agent, "_run_in_context", fake_run_in_context)
    assert asyncio.run(agent.run("first")) == "ok"
    run = agent.run_context
    assert run is not None
    run_id = run.run_id
    assert run.event_bus.subscriber_count() == 1

    assert asyncio.run(agent.run("second")) == "ok"
    assert agent.run_context is run
    assert agent.run_context.run_id == run_id
    assert answers == ["first", "second"]

    agent.close()
    assert run.closed
    assert run.event_bus.subscriber_count() == 0
    app.close()


def test_session_and_run_identity_are_explicit_and_close_cascades() -> None:
    app = ApplicationContext(config={"provider": "test"})
    session_a = app.create_session("session-a", user_id="user-a")
    session_b = app.create_session("session-b", user_id="user-b")
    run_a = session_a.create_run("run-a")
    run_b = session_b.create_run("run-b")

    assert run_a.session_id == "session-a"
    assert run_a.user_id == "user-a"
    assert run_a.tenant_id == "default"
    assert run_b.session_id == "session-b"
    assert run_a.session is not run_b.session
    assert session_a.conversation_tracker is not session_b.conversation_tracker

    session_a.close()
    assert run_a.closed
    assert not run_b.closed
    with pytest.raises(ContextClosedError):
        session_a.create_run("run-after-close")

    app.close()


def test_tenant_is_part_of_persistent_memory_and_run_identity() -> None:
    app = ApplicationContext()
    session_a = app.create_session(
        "session-a",
        user_id="same-user",
        tenant_id="tenant-a",
        memory_namespace="tenant-a:same-user",
    )
    session_b = app.create_session(
        "session-b",
        user_id="same-user",
        tenant_id="tenant-b",
        memory_namespace="tenant-b:same-user",
    )
    run_a = session_a.create_run("run-1")
    run_b = session_b.create_run("run-1")

    assert session_a.memory_namespace != session_b.memory_namespace
    assert run_a.scope_id != run_b.scope_id
    assert run_a.artifacts.scope_id != run_b.artifacts.scope_id
    app.close()


def test_memory_view_is_session_scoped_persistent_and_closed_with_session() -> None:
    app = ApplicationContext()
    session_a = app.create_session(
        "session-a",
        user_id="same-user",
        tenant_id="tenant-a",
        memory_namespace="tenant-a:same-user",
    )
    session_b = app.create_session(
        "session-b",
        user_id="same-user",
        tenant_id="tenant-a",
        memory_namespace="tenant-a:same-user",
    )

    session_a.memory_view.record_user_message("persistent fact")
    assert "persistent fact" in session_b.memory_view.get_session_context()
    session_a.close()
    with pytest.raises(RuntimeError):
        session_a.memory_view.record_user_message("late write")
    assert "persistent fact" in session_b.memory_view.get_session_context()
    app.close()


def test_memory_reset_uses_the_calling_session_tracker_only(monkeypatch) -> None:
    from agent.conversation import ConversationTracker
    from agent.memory import long_term
    from agent.memory.lifecycle import MemoryRuntime

    monkeypatch.setattr(long_term, "clear_summaries", lambda namespace: None)

    tracker_a = ConversationTracker()
    tracker_b = ConversationTracker()
    tracker_a.update(
        user_id="tenant-a:session-a",
        user_input="A goal",
        assistant_answer="A answer",
    )
    tracker_b.update(
        user_id="tenant-b:session-b",
        user_input="B goal",
        assistant_answer="B answer",
    )

    MemoryRuntime.reset(
        "tenant-a:session-a",
        conversation=True,
        facts=False,
        conversation_tracker=tracker_a,
    )

    assert tracker_a.get_events("tenant-a:session-a") == []
    assert len(tracker_b.get_events("tenant-b:session-b")) == 1
    assert tracker_b.get_state("tenant-b:session-b").last_answer == "B answer"


def test_workspace_can_publish_into_an_explicit_event_scope(tmp_path) -> None:
    from agent.workspace import WorkspaceEvent

    bus_a = EventBus(scope_id="workspace-a")
    bus_b = EventBus(scope_id="workspace-b")
    workspace_a = WorkspaceManager(event_bus=bus_a).get(tmp_path / "a")
    workspace_b = WorkspaceManager(event_bus=bus_b).get(tmp_path / "b")
    received_a: list[object] = []
    received_b: list[object] = []
    bus_a.subscribe(WorkspaceEvent.INDEX_REBUILT, received_a.append)
    bus_b.subscribe(WorkspaceEvent.INDEX_REBUILT, received_b.append)

    workspace_a.build_index()

    assert len(received_a) == 1
    assert received_b == []
    bus_a.close()
    bus_b.close()


def test_run_owned_workspace_is_isolated_and_close_preserves_root(tmp_path) -> None:
    from agent.workspace import WorkspaceEvent

    root = tmp_path / "project"
    root.mkdir()
    durable = root / "resume.txt"
    durable.write_text("checkpoint artifact", encoding="utf-8")

    app = ApplicationContext()
    session = app.create_session("session-a")
    run_a = session.create_run("run-a", workspace=root)
    run_b = session.create_run("run-b", workspace=root)
    received_a: list[object] = []
    received_b: list[object] = []
    run_a.event_bus.subscribe(WorkspaceEvent.FILE_OPENED, received_a.append)
    run_b.event_bus.subscribe(WorkspaceEvent.FILE_OPENED, received_b.append)

    run_a.workspace.record_open("resume.txt")
    assert run_a.workspace.current_context().opened_files == ["resume.txt"]
    assert run_b.workspace.current_context().opened_files == []
    assert len(received_a) == 1
    assert received_b == []

    run_a.close()
    assert durable.read_text(encoding="utf-8") == "checkpoint artifact"
    with pytest.raises(RuntimeError):
        run_a.workspace.current_context()
    assert not run_b.workspace.closed

    run_b.close()
    app.close()


def test_run_diagnostics_are_scoped_and_closeable() -> None:
    app = ApplicationContext()
    session = app.create_session("session-a", tenant_id="tenant-a")
    run_a = session.create_run("run-a")
    run_b = session.create_run("run-b")

    run_a.diagnostics.append({"run_id": run_a.run_id})
    run_b.diagnostics.append({"run_id": run_b.run_id})
    assert run_a.diagnostics.scope_id == "tenant-a:session-a:run-a"
    assert run_b.diagnostics.scope_id == "tenant-a:session-a:run-b"
    assert run_a.diagnostics != run_b.diagnostics

    run_a.close()
    with pytest.raises(RuntimeError):
        run_a.diagnostics.append({"late": True})
    assert len(run_a.diagnostics) == 1
    assert len(run_b.diagnostics) == 1
    app.close()


def test_forced_interleaving_keeps_artifacts_and_events_in_scope() -> None:
    app = ApplicationContext()
    session_a = app.create_session("session-a")
    session_b = app.create_session("session-b")
    run_a = session_a.create_run("run-a")
    run_b = session_b.create_run("run-b")
    artifact_barrier = threading.Barrier(2)
    read_barrier = threading.Barrier(2)
    event_barrier = threading.Barrier(2)
    artifacts: dict[str, str] = {}
    events_a: list[object] = []
    events_b: list[object] = []
    run_a.event_bus.subscribe("same-event", events_a.append)
    run_b.event_bus.subscribe("same-event", events_b.append)

    def write_and_read(run, label: str) -> None:
        artifact_barrier.wait()
        run.artifacts.put("text", summary=label, key="result")
        read_barrier.wait()
        artifacts[label] = run.artifacts.get_summary("result")

    def publish(run, payload: str) -> None:
        event_barrier.wait()
        run.event_bus.emit("same-event", payload)

    threads = [
        threading.Thread(target=write_and_read, args=(run_a, "A")),
        threading.Thread(target=write_and_read, args=(run_b, "B")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    event_threads = [
        threading.Thread(target=publish, args=(run_a, "A-event")),
        threading.Thread(target=publish, args=(run_b, "B-event")),
    ]
    for thread in event_threads:
        thread.start()
    for thread in event_threads:
        thread.join()

    assert artifacts == {"A": "[text] A", "B": "[text] B"}
    assert events_a == ["A-event"]
    assert events_b == ["B-event"]
    app.close()


def test_close_first_event_race_rejects_publish_deterministically() -> None:
    bus = EventBus(scope_id="race")
    bus.subscribe("event", lambda _: None)
    closed = threading.Event()
    outcome: list[str] = []

    def close_bus() -> None:
        bus.close()
        closed.set()

    def publish_after_close() -> None:
        closed.wait()
        try:
            bus.emit("event", "late")
        except EventScopeClosedError:
            outcome.append("rejected")

    threads = [
        threading.Thread(target=close_bus),
        threading.Thread(target=publish_after_close),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert outcome == ["rejected"]


def test_close_then_resume_same_run_keeps_explicit_durable_views(tmp_path) -> None:
    root = tmp_path / "durable-workspace"
    root.mkdir()
    checkpoint_store = {"checkpoint_id": "cp-1"}
    app = ApplicationContext()
    session = app.create_session("session-a")
    run = session.create_run(
        "run-a",
        workspace=root,
        checkpoint_store=checkpoint_store,
    )
    run.close()

    resumed = session.create_run(
        "run-a",
        workspace=root,
        checkpoint_store=checkpoint_store,
    )
    assert resumed.run_id == "run-a"
    assert resumed.workspace.current_workspace().root == root.resolve()
    assert resumed.checkpoint_store is checkpoint_store
    assert checkpoint_store == {"checkpoint_id": "cp-1"}
    assert root.exists()
    resumed.close()
    app.close()


def test_session_runtime_start_and_resume_keep_logical_run_identity(monkeypatch, tmp_path) -> None:
    from agent.memory.lifecycle import MemoryResetReport, MemoryRuntime
    from agent.session_runtime import SessionRuntime

    monkeypatch.setattr(
        MemoryRuntime,
        "reset",
        lambda user_id, **kwargs: MemoryResetReport(
            user_id=user_id,
            conversation=kwargs.get("conversation", True),
            facts=kwargs.get("facts", False),
        ),
    )
    runtime = SessionRuntime.create(
        session_id="session-a",
        user_id="same-user",
        tenant_id="tenant-a",
        persistent=True,
        workspace=tmp_path,
    )

    first = runtime.start_run("run-a")
    assert first.run_id == "run-a"
    assert first.tenant_id == "tenant-a"
    assert runtime.context.memory_namespace == "tenant-a:same-user"
    assert runtime.current_run is first

    second = runtime.start_run("run-b")
    assert runtime.current_run is second
    assert not first.closed

    resumed = runtime.resume_run("run-a")
    assert resumed is first
    assert resumed.run_id == "run-a"
    assert runtime.current_run is first
    runtime.destroy(purge_facts=False)


def test_session_runtime_reset_detaches_but_does_not_delete_recoverable_run(
    monkeypatch,
    tmp_path,
) -> None:
    from agent.memory.lifecycle import MemoryResetReport, MemoryRuntime
    from agent.session_runtime import SessionRuntime

    monkeypatch.setattr(
        MemoryRuntime,
        "reset",
        lambda user_id, **kwargs: MemoryResetReport(
            user_id=user_id,
            conversation=kwargs.get("conversation", True),
            facts=kwargs.get("facts", False),
        ),
    )
    runtime = SessionRuntime.create(
        session_id="session-reset",
        user_id="user-reset",
        workspace=tmp_path,
    )
    old_run = runtime.start_run("run-old")
    runtime.reset(runtime=True, conversation=True, facts=False)

    assert old_run.closed is False
    assert runtime.current_run is None
    resumed = runtime.resume_run("run-old")
    assert resumed is old_run
    runtime.destroy(purge_facts=False)
