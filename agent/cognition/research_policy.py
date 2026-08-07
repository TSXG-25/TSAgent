"""Deterministic policy for requests that require external source grounding."""

from __future__ import annotations

import re


_FINANCIAL_TERMS = re.compile(
    r"股票|股市|基金|板块|行情|证券|个股|涨停|投资|市场热点|港股|美股|A股|"
    r"加密货币|数字货币|比特币",
    re.IGNORECASE,
)
_FRESHNESS_TERMS = re.compile(
    r"近期|最近|最新|当前|今天|今日|本周|本月|热点|值得关注|实时|行情",
    re.IGNORECASE,
)
_EXPLICIT_SEARCH_TERMS = re.compile(
    r"搜索|搜一下|检索|查找|查一下|查询|找.*资料|研究|调研",
    re.IGNORECASE,
)
_DYNAMIC_TOPIC_TERMS = re.compile(
    r"天气|气温|新闻|财经|股票|股市|基金|板块|行情|证券|美股|A股|"
    r"加密货币|数字货币|比特币|量子计算|大语言模型|LLM|热点|教程|"
    r"方法|资料|文档|API|进展|动态|报告|对比|比较|怎么|如何",
    re.IGNORECASE,
)
_LOCAL_SEARCH_TERMS = re.compile(
    r"仓库|代码库|项目中|本地|文件|目录|符号|函数|类|源代码|source",
    re.IGNORECASE,
)


def is_source_grounded_request(text: str) -> bool:
    """Return true when the request must execute an external web search.

    This is intentionally broader than financial freshness detection. Explicit
    research/search requests and dynamic topics must not be delegated to an
    ``llm_executor`` that can only answer from model memory. Local repository
    searches remain outside this policy.
    """
    value = str(text or "")
    temporal_dynamic = bool(
        _FRESHNESS_TERMS.search(value) and _DYNAMIC_TOPIC_TERMS.search(value)
    )
    explicit_external = bool(
        _EXPLICIT_SEARCH_TERMS.search(value)
        and not _LOCAL_SEARCH_TERMS.search(value)
        and _DYNAMIC_TOPIC_TERMS.search(value)
    )
    return temporal_dynamic or explicit_external


def research_timeliness(text: str) -> str:
    """Map temporal language to the web tool's deterministic time filter."""
    value = str(text or "")
    if re.search(r"今天|今日|实时", value, re.IGNORECASE):
        return "today"
    if re.search(r"昨天|昨日", value, re.IGNORECASE):
        return "yesterday"
    if re.search(r"本月|最近一个月", value, re.IGNORECASE):
        return "month"
    if re.search(r"本周|最近一周", value, re.IGNORECASE):
        return "week"
    if _FRESHNESS_TERMS.search(value):
        return "week"
    return "any"


def research_query(text: str) -> str:
    """Remove the output-writing tail before sending a query to the web tool."""
    value = str(text or "").strip()
    value = re.sub(r"^\s*(?:请|帮我|麻烦)?\s*(?:搜索|搜一下|检索|查找|查一下|查询)\s*", "", value)
    value = re.split(
        r"(?:，|,)?\s*(?:然后|并且|之后|再)\s*(?:写|生成|保存|输出|制作)",
        value,
        maxsplit=1,
    )[0]
    return value.strip(" ，,。") or str(text or "").strip()


def is_fresh_research_request(text: str) -> bool:
    """Return true when a request cannot be answered from model memory alone."""
    value = str(text or "")
    return bool(_FINANCIAL_TERMS.search(value) and _FRESHNESS_TERMS.search(value))


__all__ = [
    "is_fresh_research_request",
    "is_source_grounded_request",
    "research_query",
    "research_timeliness",
]
