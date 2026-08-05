"""Session/Memory lifecycle contracts (ADR-0015)."""
import uuid


def _namespace() -> str:
    return f"test-session-{uuid.uuid4().hex}"


def test_memory_runtime_reset_is_scoped(monkeypatch):
    from agent.conversation import conversation_tracker
    from agent.memory import long_term
    from agent.memory.long_term import get_facts, save_fact
    from agent.memory.resolution import get_resolutions, record_resolution
    from agent.memory.lifecycle import MemoryRuntime
    from agent.memory.session import get_session_context, add_user_message
    from agent.memory.short_term import add_exchange, get_history

    user_id = _namespace()
    # Avoid opening a vector store in this unit test; the reset contract still
    # verifies that the semantic layer is invoked for the namespace.
    cleared_summaries = []
    monkeypatch.setattr(
        long_term,
        "clear_summaries",
        lambda value: cleared_summaries.append(value),
    )

    add_user_message(user_id, "我住在北京")
    add_exchange(user_id, "我住在北京", "已记录")
    save_fact(user_id, "personal", "location", "北京")
    record_resolution(user_id, "打开它", "output/a.py", "file")
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
    assert get_session_context(user_id) == ""
    assert get_history(user_id) == ""
    assert get_facts(user_id) == {}
    assert get_resolutions(user_id) == []
    assert conversation_tracker.get_state(user_id).turn_count == 0
    assert conversation_tracker.get_events(user_id) == []
    assert conversation_tracker.runtime_pending(user_id) is False


def test_memory_runtime_can_preserve_facts(monkeypatch):
    from agent.memory import long_term
    from agent.memory.long_term import get_facts, save_fact
    from agent.memory.lifecycle import MemoryRuntime

    user_id = _namespace()
    monkeypatch.setattr(long_term, "clear_summaries", lambda value: None)
    save_fact(user_id, "personal", "location", "上海")

    MemoryRuntime.reset(user_id, conversation=True, facts=False)
    assert get_facts(user_id)["personal"]["location"] == "上海"

    MemoryRuntime.reset(user_id, conversation=False, facts=True)
    assert get_facts(user_id) == {}


def test_session_runtime_defaults_to_isolation(monkeypatch):
    from agent.memory.lifecycle import MemoryResetReport, MemoryRuntime
    from agent.session_runtime import SessionRuntime

    calls = []

    def fake_reset(user_id, **kwargs):
        calls.append((user_id, kwargs))
        return MemoryResetReport(
            user_id=user_id,
            conversation=kwargs.get("conversation", True),
            facts=kwargs.get("facts", False),
        )

    monkeypatch.setattr(MemoryRuntime, "reset", fake_reset)
    monkeypatch.setattr(SessionRuntime, "_new_agent", lambda self: object())

    session = SessionRuntime.create(
        session_id="isolated-case",
        user_id="isolated-case",
    )
    assert session.persistent is False
    assert calls[0][1]["facts"] is True

    session.destroy()
    session.destroy()  # idempotent
    assert session.closed is True
    assert len(calls) == 2
    assert calls[-1][1]["facts"] is True
