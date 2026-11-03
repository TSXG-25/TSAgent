"""Session/Memory lifecycle contracts (ADR-0015)."""
import uuid


def _namespace() -> str:
    return f"test-session-{uuid.uuid4().hex}"


def _commit_fact(user_id: str, value: str, *, scope: str = "user"):
    from agent.memory.learning import InteractionEvidence, MemoryPolicyProjection
    from agent.memory.learning import decide_memory_learning
    from agent.memory.persistence import MemoryPersistenceBoundary

    evidence = InteractionEvidence(
        evidence_id=f"test-fact-{uuid.uuid4().hex}",
        source_kind="user_statement",
        source_ref="test:session-runtime",
        text=f"我住在{value}",
        memory_type="fact",
        requested_scope=scope,
        canonical_key="personal.location",
        value=value,
        explicit_persist=True,
        sensitive=False,
        secret=False,
        volatile=False,
    )
    policy = MemoryPolicyProjection(scope=scope, namespace=user_id)
    decision = decide_memory_learning(evidence, policy)
    return MemoryPersistenceBoundary.commit(decision, policy)


def test_memory_runtime_reset_is_scoped(monkeypatch):
    from agent.conversation import conversation_tracker
    from agent.memory import long_term
    from agent.memory.long_term import get_facts
    from agent.memory.lifecycle import MemoryRuntime

    user_id = _namespace()
    # Avoid opening a vector store in this unit test; the reset contract still
    # verifies that the semantic layer is invoked for the namespace.
    cleared_summaries = []
    monkeypatch.setattr(
        long_term,
        "clear_summaries",
        lambda value: cleared_summaries.append(value),
    )

    assert _commit_fact(user_id, "北京").committed
    conversation_tracker.update(
        user_id=user_id,
        user_input="写 output/a.py",
        assistant_answer="未完成",
        runtime_pending=True,
    )

    report = MemoryRuntime.reset(user_id, conversation=True, facts=True)

    assert report.user_id == user_id
    assert report.conversation is True and report.facts is True
    assert cleared_summaries == [user_id]
    assert get_facts(user_id) == {}
    assert conversation_tracker.get_state(user_id).turn_count == 0
    assert conversation_tracker.get_events(user_id) == []
    assert conversation_tracker.runtime_pending(user_id) is False


def test_memory_runtime_can_preserve_facts(monkeypatch):
    from agent.memory import long_term
    from agent.memory.long_term import get_facts
    from agent.memory.lifecycle import MemoryRuntime

    user_id = _namespace()
    monkeypatch.setattr(long_term, "clear_summaries", lambda value: None)
    assert _commit_fact(user_id, "上海").committed

    MemoryRuntime.reset(user_id, conversation=True, facts=False)
    assert get_facts(user_id)["personal"]["location"] == "上海"

    MemoryRuntime.reset(user_id, conversation=False, facts=True)
    assert get_facts(user_id) == {}


def test_memory_reset_clears_only_the_requested_fact_scope(tmp_path, monkeypatch):
    from agent.memory import long_term
    from agent.memory.lifecycle import MemoryRuntime
    from agent.memory.long_term import get_fact

    namespace = _namespace()
    monkeypatch.setattr(long_term, "FACTS_DB_PATH", tmp_path / "facts.sqlite3")
    long_term._init_facts_db()
    assert _commit_fact(namespace, "session-only", scope="session").committed
    assert _commit_fact(namespace, "user-level", scope="user").committed

    MemoryRuntime.reset(
        "session-a",
        conversation=False,
        facts=True,
        facts_namespace=namespace,
        facts_scope="session",
    )

    assert get_fact(namespace, "personal", "location", scope="session") is None
    assert get_fact(namespace, "personal", "location", scope="user") == "user-level"

