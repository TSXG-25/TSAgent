"""Recent conversation storage for quick recall.

Maintains an ordered log of recent exchanges per user.
This is separate from semantic memory - it stores raw text
of the last N conversations for exact recall queries.
"""
import json
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent.parent / "data"
CONVERSATION_LOG = DATA_DIR / "recent_conversations.json"
MAX_RECENT = 20


def _load_log() -> dict[str, list[dict]]:
    """Load conversation log from disk."""
    if not CONVERSATION_LOG.exists():
        return {}
    try:
        return json.loads(CONVERSATION_LOG.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, IOError):
        return {}


def _save_log(log: dict):
    """Save conversation log to disk."""
    CONVERSATION_LOG.parent.mkdir(parents=True, exist_ok=True)
    CONVERSATION_LOG.write_text(
        json.dumps(log, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def add_exchange(user_id: str, user_input: str, assistant_response: str):
    """Record a single exchange in the recent conversation log."""
    log = _load_log()
    if user_id not in log:
        log[user_id] = []
    log[user_id].append({
        "user": user_input,
        "assistant": assistant_response,
    })
    # Keep only the last MAX_RECENT entries
    if len(log[user_id]) > MAX_RECENT:
        log[user_id] = log[user_id][-MAX_RECENT:]
    _save_log(log)


def get_recent(user_id: str, n: int = 5) -> str:
    """Get the last N conversation exchanges for a user as readable text."""
    log = _load_log()
    entries = log.get(user_id, [])
    if not entries:
        return ""

    recent = entries[-n:] if n > 0 else entries
    lines = []
    for i, entry in enumerate(recent, 1):
        lines.append(f"[{i}] 用户: {entry['user']}")
        lines.append(f"    助手: {entry['assistant']}")

    return "\n\n".join(lines)