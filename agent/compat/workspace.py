"""Compatibility access to the legacy process-global Workspace service."""
from __future__ import annotations

import warnings


def get_legacy_workspace_service():
    """Return the old active-manager facade for unscoped callers only."""
    from agent.services.workspace_service import get_workspace_service

    warnings.warn(
        "legacy global WorkspaceService access; pass RunContext.workspace",
        DeprecationWarning,
        stacklevel=2,
    )
    return get_workspace_service()


def resolve_legacy_path(path: str):
    """Resolve a path for unscoped legacy callers only.

    Keeping this import here makes the production verifier independent from
    the process-global filesystem module while preserving old direct tests and
    CLI compatibility during migration.
    """
    from tools.filesystem import _resolve_path

    return _resolve_path(path)


__all__ = ["get_legacy_workspace_service", "resolve_legacy_path"]
