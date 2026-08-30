from __future__ import annotations

from pathlib import Path

import pytest

from agent.registry.tool_registry import ToolRegistry
from agent.tool_action_projection import (
    AVAILABLE_ACTIONS_PROJECTION_VERSION,
    project_available_actions,
    projection_contract_hash,
)
from agent.tool_identity import CANONICAL_TOOL_ALIASES, registry_tool_name


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_PROJECTION_HASH = (
    "eb38faa4c12a2c8f8a89ff9973c64bf17a8d7aaf11e08fe0b43bb93bff6ee3bd"
)


def _registry() -> ToolRegistry:
    registry = ToolRegistry()

    def read_file(path: str) -> str:
        """Read one file."""

        return path

    def write_file(path: str, content: str, mode: str = "overwrite") -> str:
        """Write one file."""

        return f"{path}:{content}:{mode}"

    registry.register(read_file)
    registry.register(write_file)
    return registry


def test_projection_uses_canonical_identity_and_registry_owned_schema() -> None:
    actions = project_available_actions(
        ("filesystem.read", "filesystem.write"),
        _registry(),
    )

    assert tuple(action.tool for action in actions) == (
        "filesystem.read",
        "filesystem.write",
    )
    read_schema = actions[0].args_schema
    write_schema = actions[1].args_schema
    assert set(read_schema["properties"]) == {"path"}
    assert read_schema["required"] == ["path"]
    assert set(write_schema["properties"]) == {"path", "content", "mode"}
    assert write_schema["required"] == ["path", "content"]


def test_projection_contains_only_policy_approved_actions() -> None:
    actions = project_available_actions(("filesystem.read",), _registry())

    assert len(actions) == 1
    assert actions[0].tool == "filesystem.read"


def test_projection_fails_fast_when_registry_tool_is_missing() -> None:
    with pytest.raises(ValueError, match="TOOL_PROJECTION_MISSING"):
        project_available_actions(("web_search",), _registry())


def test_projection_contract_version_and_hash_are_frozen() -> None:
    assert AVAILABLE_ACTIONS_PROJECTION_VERSION == "v2.4B-available-actions-v1"
    assert projection_contract_hash() == EXPECTED_PROJECTION_HASH


def test_canonical_alias_mapping_has_one_production_source() -> None:
    assert registry_tool_name("filesystem.read") == "read_file"
    assert registry_tool_name("web_search") == "web_search"

    duplicate_literal = '"filesystem.read": "read_file"'
    occurrences = []
    for path in (ROOT / "agent").rglob("*.py"):
        if duplicate_literal in path.read_text(encoding="utf-8"):
            occurrences.append(path.relative_to(ROOT).as_posix())
    assert occurrences == ["agent/tool_identity.py"]
    assert tuple(CANONICAL_TOOL_ALIASES) == (
        "filesystem.read",
        "filesystem.write",
        "filesystem.list",
        "filesystem.delete",
        "filesystem.move",
        "filesystem.copy",
    )
