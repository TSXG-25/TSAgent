"""Deterministic v2.3A Runtime Context ownership tests."""

import asyncio
import threading
import pytest

from agent.event_bus import EventBus, EventScopeClosedError
from agent.runtime_context import (
    ApplicationContext,
    ContextClosedError,
)
from agent.executor.plan_executor import PlanExecutor
from agent.task import ExecutionPlan, ExecutionStep, Task, Verb
from agent.services.artifact_service import (
    ArtifactScopeClosedError,
)
from agent.workspace.manager import WorkspaceManager


def _commit_terminal_run(run_context, request_text: str, answer: str) -> None:
    view = run_context.durable_store_view
    view.transition_run_with_event(
        run_status="COMPLETED",
        event_id=f"run-completed:{run_context.run_id}:{run_context.request_id}",
        event_type="run_completed",
        expected_status="RUNNING",
        timestamp="2026-01-01T00:00:01Z",
        payload={
            "request_id": run_context.request_id,
            "continuation": {
                "request": request_text,
                "goal": request_text,
                "missing": [],
                "observations": [],
                "acceptance_evidence": [],
            },
        },
        run_output={"text": answer, "evidence_ids": [], "artifact_ids": []},
    )


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


def test_repository_indexers_are_owned_by_workspace_root(tmp_path) -> None:
    root_a = tmp_path / "workspace-a"
    root_b = tmp_path / "workspace-b"
    root_a.mkdir()
    root_b.mkdir()
    app = ApplicationContext()
    session_a = app.create_session("session-a")
    session_b = app.create_session("session-b")

    run_a = session_a.create_run("run-a", workspace=root_a)
    run_b = session_b.create_run("run-b", workspace=root_b)
    same_root_indexer = app.repository_indexer_for(root_a)

    assert run_a.repository_indexer is same_root_indexer
    assert run_b.repository_indexer is not run_a.repository_indexer
    assert run_a.repository_indexer.repo_root == root_a.resolve()
    assert run_b.repository_indexer.repo_root == root_b.resolve()

    app.close()


def test_repository_search_executes_through_the_run_owned_indexer() -> None:
    class Indexer:
        ensured = False

        def ensure_built(self) -> None:
            self.ensured = True

        def search_similar(self, query: str, k: int):
            assert query == "workspace A"
            assert k == 5
            return [{"path": "src/a.py", "content": "scoped result"}]

    indexer = Indexer()
    plan = ExecutionPlan(
        task=Task(
            id="search-a",
            verb=Verb.SEARCH,
            target="workspace A",
            target_type="text",
        ),
        steps=[
            ExecutionStep(
                tool="repository",
                args={"query": "workspace A", "k": 5},
                outputs=["results"],
            )
        ],
    )

    result = asyncio.run(
        PlanExecutor().execute(plan, repository_indexer=indexer)
    )

    assert indexer.ensured is True
    assert result["results"] == "[src/a.py]\nscoped result"


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
    app = ApplicationContext()
    session_a = app.create_session("session-a", user_id="same-user")
    session_b = app.create_session("session-b", user_id="same-user")

    assert session_a.memory_namespace != session_b.memory_namespace
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
        memory_namespace="tenant-a:session-a",
        learning_namespace="tenant-a:same-user",
        memory_scope="user",
    )
    session_b = app.create_session(
        "session-b",
        user_id="same-user",
        tenant_id="tenant-a",
        memory_namespace="tenant-a:session-b",
        learning_namespace="tenant-a:same-user",
        memory_scope="user",
    )

    session_a.memory_view.get_user_facts()
    session_a.close()
    with pytest.raises(RuntimeError):
        session_a.memory_view.get_user_facts()
    app.close()


def test_service_sessions_isolate_conversation_and_share_learned_user_facts(
    monkeypatch,
    tmp_path,
) -> None:
    from agent.memory import long_term
    from agent.memory.learning import decide_memory_learning
    from agent.memory.learning_provider import (
        MemoryLearningProvider,
        MemoryLearningSelection,
        MemoryLearningSelectionEvidence,
    )
    from agent.runtime_store import SqliteRuntimeStore
    from agent.service.context_factory import ServiceContextFactory
    from agent.service.contracts import StartRunRequest

    monkeypatch.setattr(long_term, "FACTS_DB_PATH", tmp_path / "facts.sqlite3")
    long_term._init_facts_db()
    async def select_with_evidence(self, evidence, _existing, policy):
        decision = decide_memory_learning(evidence, policy)
        return MemoryLearningSelection(
            decision=decision,
            evidence=MemoryLearningSelectionEvidence(
                provider="offline-test",
                provider_path="SINGLE_PROVIDER",
                format_path="STRUCTURED_ONLY",
                raw_output=decision.to_dict(),
            ),
        )

    monkeypatch.setattr(
        MemoryLearningProvider,
        "select_with_evidence",
        select_with_evidence,
    )

    store = SqliteRuntimeStore.open(tmp_path / "runtime.sqlite3")
    factory = ServiceContextFactory(store, workspace_root=tmp_path / "workspace")

    def request(session_id: str) -> StartRunRequest:
        return StartRunRequest(
            tenant_id="tenant-memory-isolation",
            user_id="user-memory-isolation",
            session_id=session_id,
            run_id=f"run-{session_id}",
            request_id=f"request-{session_id}",
            request_text="test memory isolation",
        )

    run_a = factory.create_run(request("session-a"), run_id="run-session-a")
    run_b = factory.create_run(request("session-b"), run_id="run-session-b")
    view_a = run_a.session.memory_view
    view_b = run_b.session.memory_view

    assert view_a.namespace != view_b.namespace
    assert view_a.learning_namespace == view_b.learning_namespace

    # Conversation history derives solely from the durable run log; a
    # terminal run committed in session-a is invisible to session-b.
    _commit_terminal_run(run_a, "A-only request", "A-only answer")
    run_a2 = factory.create_run(request("session-a"), run_id="run-session-a-2")
    visible_a = run_a2.durable_store_view.conversation_results()
    assert [item["request"] for item in visible_a] == ["A-only request"]
    assert [item["answer"] for item in visible_a] == ["A-only answer"]
    assert run_b.durable_store_view.conversation_results() == []

    learned = asyncio.run(
        view_a.learn_from_interaction(
            "以后 Python 项目优先用 uv",
            memory_action="remember",
            facts=[{"category": "programming", "key": "python_environment",
                    "memory_type": "preference", "value": "uv"}],
            source_ref="run:session-a",
        )
    )

    assert learned.committed is True
    assert "python_environment: uv" in view_a.get_user_facts()
    assert "python_environment: uv" in view_b.get_user_facts()

    factory.close()


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

