"""First-party MCP utility server — deterministic offline helpers.

Runs via ``python agent/mcp/util_server.py`` (stdio).  These are small
pure functions models routinely get wrong by hand: exact counting,
validation, regex evaluation and binary-safe encodings.
"""

from __future__ import annotations

import base64
import json
import re

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("tsagent-util")


@mcp.tool()
def word_count(text: str) -> dict:
    """Exact character/word/line counts for a piece of text."""
    return {
        "chars": len(text),
        "chars_no_spaces": len(re.sub(r"\s", "", text)),
        "words": len(text.split()),
        "lines": len(text.splitlines()),
    }


@mcp.tool()
def json_format(text: str) -> str:
    """Validate JSON and return it pretty-printed, or the parse error."""
    try:
        return json.dumps(
            json.loads(text), ensure_ascii=False, indent=2, sort_keys=True
        )
    except json.JSONDecodeError as exc:
        return f"INVALID_JSON: {exc}"


@mcp.tool()
def regex_findall(pattern: str, text: str) -> dict:
    """Evaluate a Python regex and return all matches with positions."""
    try:
        matches = [
            {"match": m.group(0), "start": m.start(), "end": m.end()}
            for m in re.finditer(pattern, text)
        ]
    except re.error as exc:
        return {"error": f"INVALID_REGEX: {exc}"}
    return {"count": len(matches), "matches": matches[:50]}


@mcp.tool()
def base64_convert(text: str, direction: str) -> str:
    """Base64 encode/decode UTF-8 text; direction: 'encode'|'decode'."""
    if direction == "encode":
        return base64.b64encode(text.encode("utf-8")).decode("ascii")
    if direction == "decode":
        try:
            return base64.b64decode(text).decode("utf-8")
        except Exception:
            return "INVALID_BASE64"
    return "INVALID_DIRECTION: use 'encode' or 'decode'"


if __name__ == "__main__":
    mcp.run()
