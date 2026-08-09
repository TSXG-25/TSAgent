"""Scoped validator path helpers."""

from __future__ import annotations

from typing import Any, Mapping


def resolve_deliverable_path(
    task: Mapping[str, Any],
    deliverable: Mapping[str, Any],
    path: str,
):
    workspace = deliverable.get("_workspace") or task.get("_workspace")
    if workspace is not None:
        return workspace.resolve_path(path)
    from agent.compat.workspace import resolve_legacy_path

    return resolve_legacy_path(path)


__all__ = ["resolve_deliverable_path"]
