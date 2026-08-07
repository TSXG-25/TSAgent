"""Deterministic policy for requests that require fresh external sources."""

from __future__ import annotations

import re


_FINANCIAL_TERMS = re.compile(
    r"股票|股市|基金|板块|行情|证券|个股|涨停|投资|市场热点|港股|美股|A股",
    re.IGNORECASE,
)
_FRESHNESS_TERMS = re.compile(
    r"近期|最近|最新|当前|今天|今日|本周|本月|热点|值得关注|实时|行情",
    re.IGNORECASE,
)


def is_fresh_research_request(text: str) -> bool:
    """Return true when a request cannot be answered from model memory alone."""
    value = str(text or "")
    return bool(_FINANCIAL_TERMS.search(value) and _FRESHNESS_TERMS.search(value))


__all__ = ["is_fresh_research_request"]
