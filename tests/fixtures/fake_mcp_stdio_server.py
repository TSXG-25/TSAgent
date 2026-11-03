from __future__ import annotations

import asyncio
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP


server = FastMCP("offline-test-server")


@server.tool()
async def search(query: str, max_results: int = 5) -> str:
    if query == "slow":
        Path(os.environ["TEST_MCP_CALL_FILE"]).write_text("started")
        await asyncio.sleep(60)
    return (
        f"query={query}; max_results={max_results}\n"
        "Title: Offline fixture result\n"
        "URL: https://example.test/offline\n"
        "Summary: Synthetic result for local MCP integration testing only."
    )


if __name__ == "__main__":
    Path(os.environ["TEST_MCP_PID_FILE"]).write_text(str(os.getpid()))
    try:
        server.run()
    finally:
        Path(os.environ["TEST_MCP_STOP_FILE"]).write_text("stopped")
