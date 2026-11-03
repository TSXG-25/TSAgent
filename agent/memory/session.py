"""Session Memory (Layer 1) — 会话级工作记忆。

管理当前 session 的全部消息栈，自动注入 system prompt。
会话内存，无持久化。
"""
import datetime
from pathlib import Path
from typing import Optional

# In-memory session state per user
_sessions: dict[str, dict] = {}
MAX_MESSAGES = 30  # Keep last 30 messages in session


def ensure_session(user_id: str) -> None:
    """Ensure a session exists for the given user."""
    if user_id not in _sessions:
        _sessions[user_id] = {
            "messages": [],      # List of {"role": "user"|"assistant", "content": str}
            "created_at": datetime.datetime.now().isoformat(),
            "last_topic": "",    # Track current topic for context
        }


def add_message(user_id: str, role: str, content: str) -> None:
    """Add a message to the session stack."""
    ensure_session(user_id)
    session = _sessions[user_id]
    # Runtime records the user message at request start and records the full
    # exchange at finalization.  Collapse that intentional overlap so a
    # second turn never sees a misleading duplicated/empty history.
    if session["messages"]:
        previous = session["messages"][-1]
        if previous.get("role") == role and previous.get("content") == content:
            return
    session["messages"].append({
        "role": role,
        "content": content,
        "timestamp": datetime.datetime.now().isoformat(),
    })
    # Trim old messages
    if len(session["messages"]) > MAX_MESSAGES:
        session["messages"] = session["messages"][-MAX_MESSAGES:]


def add_user_message(user_id: str, content: str) -> None:
    """Record a user message."""
    add_message(user_id, "user", content)
    # Track topic based on first few words
    words = content.strip().split()[:3]
    _sessions[user_id]["last_topic"] = " ".join(words) if words else ""


def add_assistant_message(user_id: str, content: str) -> None:
    """Record an assistant message."""
    add_message(user_id, "assistant", content)


def get_session_context(user_id: str, n: int = 10) -> str:
    """Get the last N messages of the session as formatted text.

    Args:
        user_id: User identifier
        n: Number of recent messages to include (default 10)

    Returns:
        Formatted conversation history string.
    """
    ensure_session(user_id)
    messages = _sessions[user_id]["messages"]
    recent = messages[-n:] if n > 0 else []

    if not recent:
        return ""

    lines = [f"## 当前会话记录 (最近 {len(recent)} 条)\n"]
    for msg in recent:
        role_label = "用户" if msg["role"] == "user" else "助手"
        content = msg["content"]
        timestamp = msg.get("timestamp", "")
        # Truncate very long messages
        if len(content) > 500:
            content = content[:500] + "..."
        # Show timestamp if available
        time_tag = f"[{timestamp[:16]}] " if timestamp else ""
        lines.append(f"{time_tag}{role_label}: {content}")

    return "\n".join(lines)


def get_last_topic(user_id: str) -> str:
    """Get the current conversation topic."""
    ensure_session(user_id)
    return _sessions[user_id].get("last_topic", "")


def clear_session(user_id: str) -> None:
    """Clear the session for a user."""
    _sessions.pop(user_id, None)


def get_message_count(user_id: str) -> int:
    """Get the number of messages in the current session."""
    ensure_session(user_id)
    return len(_sessions[user_id]["messages"])
