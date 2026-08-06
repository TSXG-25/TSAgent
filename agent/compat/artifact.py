"""Compatibility access to the legacy process-global ArtifactService."""
from __future__ import annotations

import warnings


def get_legacy_artifact_service():
    from agent.services.artifact_service import ArtifactService

    warnings.warn(
        "legacy global ArtifactService access; pass RunContext.artifacts",
        DeprecationWarning,
        stacklevel=2,
    )
    return ArtifactService


__all__ = ["get_legacy_artifact_service"]
