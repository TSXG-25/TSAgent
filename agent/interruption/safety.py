"""Deterministic cancellation-safety classification for execution operations."""

from __future__ import annotations

from .contracts import CancellationSafetyClass


_INTERRUPTIBLE_TOOLS = {
    "llm",
    "web_search",
    "web_deep_search",
    "web_news_search",
    "web_fetch",
}


def tool_cancellation_safety(tool_name: str) -> CancellationSafetyClass:
    """Return a conservative safety class for a registered operation."""

    name = str(tool_name).strip().lower()
    if name in _INTERRUPTIBLE_TOOLS or name.startswith("provider."):
        return CancellationSafetyClass.INTERRUPTIBLE
    return CancellationSafetyClass.BOUNDARY_ONLY


__all__ = ["tool_cancellation_safety"]
