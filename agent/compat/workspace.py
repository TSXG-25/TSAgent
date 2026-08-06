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


__all__ = ["get_legacy_workspace_service"]
