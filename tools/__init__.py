# tools/__init__.py
"""Tools package for TSAgent.

Each module in this package registers one or more tools with the global
ToolRegistry via `registry.register()` at import time.

Available tool modules:
- shell: Execute shell commands in Docker sandbox
- filesystem: Read/write/list files
- web: Web search and fetch
- patch: Generate and apply patches
- python: Execute Python code
- memory: Query agent memory
- workflow: Manage workflows
"""

from agent.registry.tool_registry import registry

__all__ = ["registry"]