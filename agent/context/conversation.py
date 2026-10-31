"""Durable-run conversation derivation.

The SQLite run log is the single source of model-visible conversation
history.  ``derive_conversation`` is the one pure projection from terminal
run projections (``DurableRuntimeStoreView.conversation_results``) to
role/content message pairs; ``render_conversation_text`` renders the same
messages as untrusted prompt text.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

HISTORY_CHAR_BUDGET = 16000
ANSWER_CHAR_CAP = 2000
OBSERVATION_LIMIT = 5
OBSERVATION_CHAR_CAP = 300


def _observation_text(observation: Mapping[str, Any]) -> str:
    result = observation.get("result")
    if isinstance(result, Mapping):
        content = str(result.get("content", "") or "").strip()
        if content:
            return content
        return str(result.get("error_code", "") or "")
    return str(observation.get("summary", "") or observation.get("content", "") or "")


def derive_conversation(
    run_results: Sequence[Mapping[str, Any]],
) -> list[dict[str, str]]:
    """Project terminal runs into user/assistant message pairs, oldest first."""
    pairs: list[list[dict[str, str]]] = []
    for run in run_results:
        user_text = str(run.get("request", "") or run.get("goal", "") or "")
        parts = [str(run.get("answer", "") or "")[:ANSWER_CHAR_CAP]]
        artifacts = [
            str(item.get("path", ""))
            for item in run.get("artifacts", []) or []
            if item.get("path")
        ]
        if artifacts:
            parts.append("\n[产物] " + ", ".join(artifacts))
        observations = [
            text
            for text in (
                _observation_text(item)
                for item in (run.get("observations", []) or [])[:OBSERVATION_LIMIT]
            )
            if text
        ]
        for text in observations:
            parts.append("\n[观察] " + text[:OBSERVATION_CHAR_CAP])
        status = str(run.get("status", "") or "")
        if status != "COMPLETED":
            parts.append(f"\n[状态] {status}")
            missing = [str(item) for item in run.get("missing", []) or []]
            if missing:
                parts.append("\n未完成: " + ", ".join(missing))
        pairs.append([
            {"role": "user", "content": user_text},
            {"role": "assistant", "content": "".join(parts)},
        ])

    def total_chars() -> int:
        return sum(len(message["content"]) for pair in pairs for message in pair)

    while len(pairs) > 1 and total_chars() > HISTORY_CHAR_BUDGET:
        pairs.pop(0)

    return [message for pair in pairs for message in pair]


def render_conversation_text(messages: Sequence[Mapping[str, str]]) -> str:
    """Render derived messages as untrusted ``用户:/助手:`` lines."""
    if not messages:
        return ""
    labels = {"user": "用户", "assistant": "助手"}
    return "\n".join(
        f"{labels.get(str(message.get('role', '')), '?')}: {message.get('content', '')}"
        for message in messages
    )
