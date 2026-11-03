"""Deterministic Runtime gates shared by Planner, Runtime and Service.

These helpers intentionally inspect structured Runtime state only.  Natural
language can request an output or fresh research, but prose from an LLM never
establishes that either requirement was satisfied.
"""

from __future__ import annotations

import unicodedata
from typing import Any, Mapping

_FRESH_TOOLS = frozenset({
    "web_search",
    "web_news_search",
    "web_deep_search",
    "web_fetch",
})
def is_empty_or_punctuation_request(text: str) -> bool:
    """Recognize input that cannot carry a task or question.

    This is intentionally narrower than ``not text``: symbols such as ``+``
    may be meaningful in a programming or math request. Unicode punctuation
    alone (for example ``？`` or ``...``) is rejected before Planner/LLM work.
    """

    value = str(text or "").strip()
    if not value:
        return True
    return all(
        char.isspace() or unicodedata.category(char).startswith("P")
        for char in value
    )


def _observation_tools(observation: Mapping[str, Any]) -> set[str]:
    tools: set[str] = set()
    value = observation.get("tools")
    if isinstance(value, (list, tuple, set)):
        tools.update(str(item) for item in value)
    for key in ("tool", "tool_used", "executor"):
        item = observation.get(key)
        if item:
            tools.add(str(item))
    return tools


def has_fresh_evidence(state: Mapping[str, Any]) -> bool:
    """Return true only for a successful observation with verifiable sources.

    Fresh evidence is a structured fact: the observation of a source tool
    must carry non-empty ``sources`` URLs produced by the tool result, never
    inferred from prose.
    """

    if bool(state.get("fresh_evidence")):
        return True
    for task in state.get("plan", []) or ():
        if not isinstance(task, Mapping):
            continue
        for observation in task.get("observations", []) or ():
            if not isinstance(observation, Mapping):
                continue
            if str(observation.get("status", "")) != "succeeded":
                continue
            if _observation_tools(observation) & _FRESH_TOOLS:
                sources = observation.get("sources")
                return bool(
                    isinstance(sources, (list, tuple, set))
                    and any(str(url).strip() for url in sources)
                )
    return False


def freshness_required_for(state: Mapping[str, Any] | None = None) -> bool:
    """The Intent declares freshness; default to false without state."""

    if state is not None and "freshness_required" in state:
        return bool(state.get("freshness_required"))
    return False


def output_required(state: Mapping[str, Any] | None = None) -> bool:
    """All public answer-producing Runs require a non-empty user output."""

    if state is None:
        return True
    return bool(state.get("answer_required", True))


__all__ = [
    "freshness_required_for",
    "has_fresh_evidence",
    "is_empty_or_punctuation_request",
    "output_required",
]
