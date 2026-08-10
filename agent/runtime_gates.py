"""Deterministic Runtime gates shared by Planner, Runtime and Service.

These helpers intentionally inspect structured Runtime state only.  Natural
language can request an output or fresh research, but prose from an LLM never
establishes that either requirement was satisfied.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Mapping

from agent.cognition.research_policy import is_fresh_research_request


_FRESH_TOOLS = frozenset({
    "web_search",
    "web_news_search",
    "web_deep_search",
    "web_fetch",
})
_PREVIOUS_OUTPUT_PATTERNS = (
    re.compile(r"^输出呢[？?。!！…]*$", re.IGNORECASE),
    re.compile(r"^(?:结果|答案)呢[？?。!！…]*$", re.IGNORECASE),
    re.compile(r"^(?:刚才的)?(?:结果|输出|答案)呢[？?。!！…]*$", re.IGNORECASE),
    re.compile(r"^(?:给我看|展示|显示)(?:刚才的|上一轮的|上次的)?输出[？?。!！…]*$", re.IGNORECASE),
    re.compile(r"^(?:你还没回答|还没回答呢)[。!！…]*$", re.IGNORECASE),
    re.compile(r"^(?:show|display|give me) (?:the )?(?:last )?(?:output|result|answer)[.!?]*$", re.IGNORECASE),
)


def is_previous_output_request(text: str) -> bool:
    """Recognize a deterministic request to retrieve a prior RunOutput."""

    value = re.sub(r"\s+", "", str(text or "").strip())
    return any(pattern.fullmatch(value) for pattern in _PREVIOUS_OUTPUT_PATTERNS)


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
    for key in ("tool", "executor"):
        item = observation.get(key)
        if item:
            tools.add(str(item))
    return tools


def has_fresh_evidence(state: Mapping[str, Any]) -> bool:
    """Return true only for a successful, source-tool-backed observation."""

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
                return bool(str(observation.get("summary", "")).strip())
    return False


def freshness_required_for(text: str, state: Mapping[str, Any] | None = None) -> bool:
    """Use explicit state when present, otherwise derive the safe default."""

    if state is not None and "freshness_required" in state:
        return bool(state.get("freshness_required"))
    return is_fresh_research_request(text)


def output_required(state: Mapping[str, Any] | None = None) -> bool:
    """All public answer-producing Runs require a non-empty user output."""

    if state is None:
        return True
    return bool(state.get("answer_required", True))


__all__ = [
    "freshness_required_for",
    "has_fresh_evidence",
    "is_empty_or_punctuation_request",
    "is_previous_output_request",
    "output_required",
]
