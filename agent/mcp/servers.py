"""MCP server registry — one spec row per configured server.

Adding a server means adding a ``MCPServerSpec`` here; transports, lazy
service start, availability checks and error codes live in
``agent.mcp.client``.
"""

from __future__ import annotations

import os

from .client import MCPServerSpec


def _ddg_env() -> dict[str, str]:
    return {
        # Keep real URLs in search output because the MCP process is scoped
        # to this tool call, so its in-memory ref:// tokens would not survive.
        "DDG_REF_URL_THRESHOLD": "0",
        "DDG_SEARCH_BACKEND": "auto",
        # Forward the opt-in SSRF bypass: under fake-IP DNS (Clash/V2Ray TUN
        # mode) every domain resolves to 198.18.x.x and the server's
        # private-address check rejects all fetches.
        "DDG_ALLOW_PRIVATE_URLS": os.environ.get("DDG_ALLOW_PRIVATE_URLS", "0"),
    }


DUCKDUCKGO = MCPServerSpec(
    name="duckduckgo",
    title="DuckDuckGo",
    transport="stdio",
    command="duckduckgo-mcp-server",
    env=_ddg_env,
    missing_hint=(
        "duckduckgo-mcp-server is not installed; "
        "install the project's requirements"
    ),
    unavailable_code="RESEARCH_TOOL_UNAVAILABLE",
    session_timeout=90,
    # Internal-only: web_search/web_fetch wrappers add parsing, dedup and
    # freshness handling on top of the raw server tools.
    expose_tools=(),
)

REME = MCPServerSpec(
    name="reme",
    title="ReMe",
    transport="http",
    url=os.environ.get("TSAGENT_REME_URL", "http://127.0.0.1:2333/mcp"),
    spawn_command=("reme", "start"),
    spawn_ready_timeout=45.0,
    spawn_hint=(
        "REME_UNAVAILABLE: `reme` not on PATH "
        "(install with: pip install 'reme-ai[core]')"
    ),
    unavailable_code="REME_UNAVAILABLE",
    tool_error_code="REME_TOOL_FAILED",
    # Internal-only: reme_search keeps its MEMORY_UNAVAILABLE error code;
    # auto_memory is a write path driven by the Runtime, not the model.
    expose_tools=(),
)

# Official Python time server (mcp-server-time).  Read-only timezone
# queries — the first server exposed to the model through the unified
# catalog: the allowlist is the whole authorization decision.
TIME = MCPServerSpec(
    name="time",
    title="Time",
    transport="stdio",
    command="mcp-server-time",
    missing_hint=(
        "mcp-server-time is not installed; "
        "install the project's requirements"
    ),
    unavailable_code="MCP_UNAVAILABLE",
    tool_error_code="MCP_TOOL_FAILED",
    session_timeout=30,
    expose_tools=("get_current_time", "convert_time"),
)

# Official Node sequential-thinking server via npx (no install needed;
# npx resolves the package on first use).  Pure reasoning scratchpad.
SEQUENTIAL_THINKING = MCPServerSpec(
    name="thinking",
    title="Sequential Thinking",
    transport="stdio",
    command="npx",
    args=("-y", "@modelcontextprotocol/server-sequential-thinking"),
    missing_hint="npx (Node.js) is not installed",
    unavailable_code="MCP_UNAVAILABLE",
    tool_error_code="MCP_TOOL_FAILED",
    session_timeout=120,
    expose_tools=("sequentialthinking",),
)

# First-party utility server (agent/mcp/util_server.py) — deterministic
# offline helpers the model routinely gets wrong by hand.  command is the
# current interpreter so the server always matches this env's mcp version.
import sys as _sys
from pathlib import Path as _Path

UTIL = MCPServerSpec(
    name="util",
    title="TSAgent Utilities",
    transport="stdio",
    command=_sys.executable,
    args=(str(_Path(__file__).with_name("util_server.py")),),
    missing_hint="python interpreter not found",
    unavailable_code="MCP_UNAVAILABLE",
    tool_error_code="MCP_TOOL_FAILED",
    session_timeout=30,
    expose_tools=("word_count", "json_format", "regex_findall", "base64_convert"),
)

# Document → Markdown conversion (Microsoft markitdown).
MARKITDOWN = MCPServerSpec(
    name="markitdown",
    title="MarkItDown",
    transport="stdio",
    command="markitdown-mcp",
    missing_hint="markitdown-mcp is not installed",
    unavailable_code="MCP_UNAVAILABLE",
    tool_error_code="MCP_TOOL_FAILED",
    session_timeout=60,
    expose_tools=("convert_to_markdown",),
)

# Excel workbook read/write/format (excel-mcp-server; stdio subcommand).
EXCEL = MCPServerSpec(
    name="excel",
    title="Excel",
    transport="stdio",
    command="excel-mcp-server",
    args=("stdio",),
    missing_hint="excel-mcp-server is not installed",
    unavailable_code="MCP_UNAVAILABLE",
    tool_error_code="MCP_TOOL_FAILED",
    session_timeout=60,
    expose_tools=(
        "read_data_from_excel",
        "write_data_to_excel",
        "create_workbook",
        "create_worksheet",
        "format_range",
        "apply_formula",
        "create_chart",
        "create_table",
        "create_pivot_table",
        "merge_cells",
        "get_workbook_metadata",
    ),
)

# Bilibili read-side — Pluto motif exploration platform adapter.
BILIBILI = MCPServerSpec(
    name="bilibili",
    title="Bilibili Read",
    transport="stdio",
    command=_sys.executable,
    args=(str(_Path(__file__).with_name("bilibili_server.py")),),
    missing_hint="python interpreter not found",
    unavailable_code="MCP_UNAVAILABLE",
    tool_error_code="MCP_TOOL_FAILED",
    session_timeout=30,
    expose_tools=(
        "bili_search",
        "bili_video_info",
        "bili_comments",
        "bili_subtitle",
    ),
)

SERVERS: dict[str, MCPServerSpec] = {
    spec.name: spec for spec in (
        DUCKDUCKGO, REME, TIME, SEQUENTIAL_THINKING, UTIL, MARKITDOWN, EXCEL,
        BILIBILI
    )
}
