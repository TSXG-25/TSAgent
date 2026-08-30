"""Read-only Tool capability projection for NextAction selection."""

from __future__ import annotations

from collections.abc import Sequence
import hashlib
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agent.tool_identity import registry_tool_name


AVAILABLE_ACTIONS_PROJECTION_VERSION = "v2.4B-available-actions-v1"


class ToolActionProjection(BaseModel):
    """One policy-approved canonical Tool and its Registry-owned schema."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tool: str
    args_schema: dict[str, Any] = Field(default_factory=dict)


def project_available_actions(
    canonical_tools: Sequence[str],
    registry: Any,
) -> tuple[ToolActionProjection, ...]:
    """Project policy-approved Tools without exposing the Registry itself."""

    actions: list[ToolActionProjection] = []
    for canonical_name in canonical_tools:
        implementation_name = registry_tool_name(canonical_name)
        tool = registry.get(implementation_name)
        if tool is None:
            raise ValueError(
                f"TOOL_PROJECTION_MISSING: {canonical_name} -> {implementation_name}"
            )
        schema_model = tool.args_schema
        if schema_model is None:
            raise ValueError(f"TOOL_SCHEMA_MISSING: {canonical_name}")
        actions.append(ToolActionProjection(
            tool=canonical_name,
            args_schema=schema_model.model_json_schema(),
        ))
    return tuple(actions)


def projection_contract_hash() -> str:
    """Hash the explicit projection envelope independently of Tool instances."""

    envelope = {
        "version": AVAILABLE_ACTIONS_PROJECTION_VERSION,
        "schema": ToolActionProjection.model_json_schema(),
    }
    canonical = json.dumps(
        envelope,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = [
    "AVAILABLE_ACTIONS_PROJECTION_VERSION",
    "ToolActionProjection",
    "project_available_actions",
    "projection_contract_hash",
]
