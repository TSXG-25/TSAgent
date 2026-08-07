"""Stable error classification for Runtime recovery decisions.

Some failures are deterministic boundary violations or unavailable required
capabilities.  Replanning them only repeats the same failure and burns LLM
budget, so they must be visible to Runtime as non-retriable facts.
"""

from __future__ import annotations


NON_RETRIABLE_CODES = frozenset({
    "PROTECTED_INTERNAL_PATH",
    "UNSUPPORTED_BINARY",
    "IDENTITY_MISMATCH",
    "RESEARCH_TOOL_UNAVAILABLE",
})


def classify_execution_error(error: object) -> str:
    """Map tool/compiler text to a stable non-retriable error code."""
    text = str(error or "")
    upper = text.upper()
    for code in NON_RETRIABLE_CODES:
        if code in upper:
            return code
    if "网络搜索功能不可用" in text or "未找到关于" in text:
        return "RESEARCH_TOOL_UNAVAILABLE"
    if "无法解码文件" in text or "请确保文件是文本格式" in text:
        return "UNSUPPORTED_BINARY"
    if "OFFICE 二进制" in text or "OFFICE BINARY" in upper:
        return "UNSUPPORTED_BINARY"
    return ""


def is_non_retriable(code: str) -> bool:
    return str(code or "") in NON_RETRIABLE_CODES


__all__ = ["NON_RETRIABLE_CODES", "classify_execution_error", "is_non_retriable"]
