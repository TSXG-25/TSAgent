"""Canonical execution Tool identity and Registry alias resolution."""

from __future__ import annotations

from types import MappingProxyType


CANONICAL_TOOL_ALIASES = MappingProxyType({
    "filesystem.read": "read_file",
    "filesystem.write": "write_file",
    "filesystem.list": "list_directory",
    "filesystem.delete": "delete_file",
    "filesystem.move": "move_file",
    "filesystem.copy": "copy_file",
})


def registry_tool_name(canonical_name: str) -> str:
    """Resolve one canonical execution identity to its Registry name."""

    return CANONICAL_TOOL_ALIASES.get(canonical_name, canonical_name)


__all__ = ["CANONICAL_TOOL_ALIASES", "registry_tool_name"]
