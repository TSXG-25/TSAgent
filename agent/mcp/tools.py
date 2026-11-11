"""Expose MCP server tools to the agent loop's unified tool catalog.

A spec row opts tools in via ``expose_tools`` — exposure is an explicit
contract decision (tool schemas enter the model's catalog verbatim), not
an accident of connecting a server.  Discovered tools dispatch straight
to ``call_mcp_tool``; they bypass ``EffectAuthorization`` the same way
read-only loop builtins do, so the allowlist IS the authorization gate —
keep it to tools the Run is allowed to invoke.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from .client import MCPServerSpec, list_tools
from .servers import SERVERS


@dataclass(frozen=True)
class ExposedTool:
    """One server tool selected for the model-visible catalog."""

    spec: MCPServerSpec
    tool_name: str  # server-side name
    description: str
    input_schema: Mapping[str, Any] = field(default_factory=dict)

    @property
    def catalog_name(self) -> str:
        return f"mcp_{self.spec.name}_{self.tool_name}"


_tool_cache: dict[str, list[Any]] = {}
_DISK_CACHE = Path.home() / ".tsagent" / "mcp_catalog.json"
_disk_loaded = False


def _spec_sig(spec) -> str:
    return "|".join((
        spec.name, spec.transport, spec.command,
        ",".join(spec.args), spec.url, ",".join(spec.expose_tools),
    ))


def _load_disk() -> dict:
    global _disk_loaded
    _disk_loaded = True
    try:
        import json
        return json.loads(_DISK_CACHE.read_text())
    except Exception:
        return {}


def _save_disk(entries: dict) -> None:
    try:
        import json
        _DISK_CACHE.parent.mkdir(parents=True, exist_ok=True)
        _DISK_CACHE.write_text(json.dumps(entries, ensure_ascii=False))
    except Exception:
        pass


async def discover_exposed_tools(
    specs: Iterable[MCPServerSpec] | None = None,
) -> list[ExposedTool]:
    """List each opted-in server's tools; unavailable servers yield none."""
    # 发现并行化：stdio server 各需 1-4s 启动，串行叠加会拖慢
    # build_toolset（首轮 run 延迟可超 10s）；失败 raise、不缓存，
    # 不影响其他 server 且下一次发现会重试。
    pending = [
        spec for spec in (specs if specs is not None else SERVERS.values())
        if spec.expose_tools
    ]
    global _disk_loaded
    if not _disk_loaded:
        _tool_cache.update(_load_disk())

    uncached = [s for s in pending if _spec_sig(s) not in _tool_cache]
    if uncached:
        results = await asyncio.gather(
            *(list_tools(spec) for spec in uncached),
            return_exceptions=True,
        )
        dirty = False
        for spec, result in zip(uncached, results):
            if not isinstance(result, list):
                # 发现失败 — 不缓存，下一次发现会重试。未安装与
                # 无导出工具的 [] 是成功结果，照常缓存。
                continue
            wanted = set(spec.expose_tools)
            _tool_cache[_spec_sig(spec)] = [
                {
                    "name": t.name,
                    "description": str(getattr(t, "description", "") or ""),
                    "inputSchema": dict(getattr(t, "inputSchema", None) or {}),
                }
                for t in result if t.name in wanted
            ]
            dirty = True
        if dirty:
            _save_disk(_tool_cache)
    discovered: list[ExposedTool] = []
    for spec in pending:
        for tool in _tool_cache.get(_spec_sig(spec), []):
            discovered.append(
                ExposedTool(
                    spec=spec,
                    tool_name=tool["name"],
                    description=tool["description"]
                    or f"{spec.label} MCP tool {tool['name']}",
                    input_schema=dict(tool["inputSchema"]),
                )
            )
    return discovered


def clear_tool_cache() -> None:
    """清空内存与磁盘缓存（测试语义上的"全新状态"）。"""
    global _disk_loaded
    _disk_loaded = False
    _tool_cache.clear()
    _DISK_CACHE.unlink(missing_ok=True)
