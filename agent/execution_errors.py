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
    "UNKNOWN_TOOL",
    "UNSUPPORTED_CAPABILITY",
    "FILE_OPERATION_FAILED",
    "FILE_WRITE_UNVERIFIED",
    "FILE_OPERATION_UNVERIFIED",
    "EMPTY_WRITE_CONTENT",
    "PRESERVATION_VIOLATION",
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
    if "UNKNOWN_TOOL" in upper or "未找到工具" in text or "工具不存在" in text:
        return "UNKNOWN_TOOL"
    error_type = type(error).__name__.upper()
    if isinstance(error, TimeoutError) or "TIMEOUT" in error_type or "TIMED OUT" in upper or "超时" in text:
        return "PROVIDER_TIMEOUT"
    if "所有 LLM 提供商均不可用" in text or "PROVIDER_UNAVAILABLE" in upper:
        return "PROVIDER_UNAVAILABLE"
    if (
        "CONNECTION" in upper
        or "NETWORK" in upper
        or "DNS" in upper
        or "连接" in text
        or "网络" in text
    ):
        return "PROVIDER_NETWORK"
    if (
        "RESPONSE_FORMAT" in upper
        or "BAD REQUEST" in upper
        or "INVALID REQUEST" in upper
        or "STATUS CODE 400" in upper
        or "HTTP 400" in upper
    ):
        return "PROVIDER_REQUEST_INVALID"
    return ""


def is_non_retriable(code: str) -> bool:
    return str(code or "") in NON_RETRIABLE_CODES


__all__ = ["NON_RETRIABLE_CODES", "classify_execution_error", "is_non_retriable"]
