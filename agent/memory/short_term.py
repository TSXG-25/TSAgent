"""Short-Term Memory (Layer 2) — 最近 N 轮对话 + 自动压缩。

始终注满上下文，不做关键词选择性注入。
超过窗口的对话自动触发 LLM 压缩总结，并将摘要写入长时记忆。
"""
import json
from pathlib import Path
from typing import Optional, Any

from langchain_core.messages import SystemMessage, HumanMessage
from agent.llm import llm

# Config
SHORT_TERM_WINDOW = 6  # Keep last 6 exchanges (12 messages)
COMPRESS_THRESHOLD = 10  # Trigger compression after 10 exchanges

# Storage
DATA_DIR = Path(__file__).parent.parent.parent / "data"
ST_DIR = DATA_DIR / "short_term"
ST_DIR.mkdir(parents=True, exist_ok=True)


def _store_path(user_id: str) -> Path:
    return ST_DIR / f"{user_id}.json"


def _load(user_id: str) -> list[dict]:
    """Load short-term conversation history."""
    p = _store_path(user_id)
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, IOError):
        return []


def _save(user_id: str, data: list[dict]) -> None:
    """Save short-term conversation history."""
    _store_path(user_id).write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def add_exchange(user_id: str, user_input: str, assistant_response: str) -> None:
    """Add a conversation exchange to short-term memory."""
    from datetime import datetime
    history = _load(user_id)
    history.append({
        "user": user_input,
        "assistant": assistant_response,
        "timestamp": datetime.now().isoformat(),
    })

    # Check if compression is needed
    if len(history) > COMPRESS_THRESHOLD:
        # Keep only last SHORT_TERM_WINDOW, compress the rest
        to_compress = history[:-SHORT_TERM_WINDOW]
        history = history[-SHORT_TERM_WINDOW:]
        # Trigger async compression (fire-and-forget via executor)
        _trigger_compression(user_id, to_compress)

    _save(user_id, history)


async def compress_history(user_id: str, entries: list[dict]) -> str:
    """Use LLM to compress conversation history into a summary.

    Args:
        user_id: User identifier
        entries: List of conversation exchanges to compress

    Returns:
        A concise summary string.
    """
    if not entries:
        return ""

    # Format the history
    text_lines = []
    for i, e in enumerate(entries):
        text_lines.append(f"用户: {e['user']}")
        text_lines.append(f"助手: {e['assistant']}")
    history_text = "\n".join(text_lines)

    prompt = f"""你是一个对话摘要生成器。将以下对话历史压缩为一段简洁的摘要。

要求：
- 提取关键信息：讨论了什么话题、问了什么问题、做了什么操作。
- 保持事实准确，不添加未见过的信息。
- 用 3-5 句话总结。
- 使用中文。

对话历史：
{history_text[:3000]}

摘要："""

    response = await llm.ainvoke([HumanMessage(content=prompt)])
    return response.content.strip()


def _trigger_compression(user_id: str, entries: list[dict]) -> None:
    """Fire-and-forget compression. Stores result in long-term memory."""
    import asyncio
    import traceback

    async def _do_compress():
        try:
            summary = await compress_history(user_id, entries)
            if summary:
                # Store in long-term memory
                from agent.memory.long_term import store_summary
                store_summary(user_id, summary)
        except Exception:
            traceback.print_exc()

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_do_compress())
    except RuntimeError:
        asyncio.run(_do_compress())


def get_history(user_id: str, n: int | None = None) -> str:
    """Get the short-term conversation history as formatted text.

    Args:
        user_id: User identifier
        n: Number of recent exchanges (default: all within window)

    Returns:
        Formatted conversation history or empty string.
    """
    entries = _load(user_id)
    if not entries:
        return ""

    recent = entries[-n:] if n and n > 0 else entries
    lines = []
    for i, e in enumerate(recent, 1):
        timestamp = e.get("timestamp", "")
        time_tag = f"[{timestamp[:16]}] " if timestamp else ""
        lines.append(f"{time_tag}[{i}] 用户: {e['user']}")
        lines.append(f"       助手: {e['assistant']}")

    return "\n\n".join(lines)


def get_latest_exchanges(user_id: str, n: int = 3) -> Optional[str]:
    """Get the latest N exchanges for quick recall (always injected)."""
    return get_history(user_id, n=n)