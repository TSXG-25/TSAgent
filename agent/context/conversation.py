"""Durable-run conversation derivation.

The SQLite run log is the single source of model-visible conversation
history.  ``derive_pairs`` projects terminal run projections
(``DurableRuntimeStoreView.conversation_results``) into user/assistant
pairs; ``flatten_pairs`` renders them (plus an optional durable summary)
into role/content messages.  ``split_pairs_for_budget`` chooses which
oldest pairs a durable summary must cover so nothing is dropped silently.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

HISTORY_CHAR_BUDGET = 16000
ANSWER_CHAR_CAP = 2000
OBSERVATION_LIMIT = 5
OBSERVATION_CHAR_CAP = 300
SUMMARY_CHAR_CAP = 4000


def _observation_text(observation: Mapping[str, Any]) -> str:
    result = observation.get("result")
    if isinstance(result, Mapping):
        content = str(result.get("content", "") or "").strip()
        if content:
            return content
        return str(result.get("error_code", "") or "")
    return str(observation.get("summary", "") or observation.get("content", "") or "")


def derive_pairs(
    run_results: Sequence[Mapping[str, Any]],
) -> list[dict[str, str]]:
    """Project terminal runs into user/assistant pairs, oldest first."""
    pairs: list[dict[str, str]] = []
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
        pairs.append({
            "run_id": str(run.get("run_id", "") or ""),
            "updated_at": str(run.get("updated_at", "") or ""),
            "user": user_text,
            "assistant": "".join(parts),
        })
    return pairs


def _pair_chars(pair: Mapping[str, str]) -> int:
    return len(pair["user"]) + len(pair["assistant"])


def split_pairs_for_budget(
    pairs: Sequence[Mapping[str, str]],
    summary: str = "",
    budget: int = HISTORY_CHAR_BUDGET,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Return (pairs the summary must cover, pairs kept verbatim).

    The kept tail always fits the budget after subtracting the summary.
    At least the newest pair is kept verbatim.
    """
    kept: list[dict[str, str]] = [dict(pair) for pair in pairs]
    compact: list[dict[str, str]] = []

    def total() -> int:
        return min(len(summary), SUMMARY_CHAR_CAP) + sum(
            _pair_chars(pair) for pair in kept
        )

    while len(kept) > 1 and total() > budget:
        compact.append(kept.pop(0))
    return compact, kept


def flatten_pairs(
    pairs: Sequence[Mapping[str, str]],
    summary: str = "",
) -> list[dict[str, str]]:
    """Render summary + pairs into role/content messages, oldest first."""
    messages: list[dict[str, str]] = []
    if summary.strip():
        messages.append({
            "role": "user",
            "content": f"[早前对话摘要] {summary[:SUMMARY_CHAR_CAP]}",
        })
        messages.append({
            "role": "assistant",
            "content": "（摘要记录了更早的对话要点）",
        })
    for pair in pairs:
        messages.append({"role": "user", "content": pair["user"]})
        messages.append({"role": "assistant", "content": pair["assistant"]})
    return messages


def derive_conversation(
    run_results: Sequence[Mapping[str, Any]],
    summary: str = "",
) -> list[dict[str, str]]:
    """Full history projection: summary prefix + verbatim pairs.

    No budget truncation happens here; callers that need bounded history
    use ``split_pairs_for_budget`` and persist a summary for the prefix.
    """
    return flatten_pairs(derive_pairs(run_results), summary)


def render_conversation_text(messages: Sequence[Mapping[str, str]]) -> str:
    """Render derived messages as untrusted ``用户:/助手:`` lines."""
    if not messages:
        return ""
    labels = {"user": "用户", "assistant": "助手"}
    return "\n".join(
        f"{labels.get(str(message.get('role', '')), '?')}: {message.get('content', '')}"
        for message in messages
    )
