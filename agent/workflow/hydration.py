"""Hydrate file-backed Workflow artifacts after a process restart.

Checkpoint snapshots intentionally retain digests and references rather than
full artifact bodies.  This module reconstructs only artifacts whose
references can be read and whose content digest still matches the snapshot.
It never treats a missing or mismatched reference as an empty artifact.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .artifact import Artifact
from .context import ExecutionContext


@dataclass(frozen=True)
class ArtifactHydrationReport:
    hydrated_types: tuple[str, ...] = ()
    missing_types: tuple[str, ...] = ()
    mismatched_types: tuple[str, ...] = ()
    unavailable_types: tuple[str, ...] = ()

    @property
    def integrity_errors(self) -> tuple[str, ...]:
        return self.missing_types + self.mismatched_types

    @property
    def unresolved_types(self) -> tuple[str, ...]:
        return self.missing_types + self.mismatched_types + self.unavailable_types


def _metadata(snapshot) -> dict[str, str]:
    return {str(key): str(value) for key, value in snapshot.metadata}


def _hydrate_snapshot(
    snapshot,
    context: ExecutionContext,
    *,
    reference_override: str = "",
) -> str:
    """Hydrate one snapshot and return its outcome category."""
    metadata = _metadata(snapshot)
    reference = str(
        reference_override
        or snapshot.reference
        or metadata.get("reference", "")
    )
    artifact_type = str(snapshot.artifact_type or metadata.get("artifact_type", ""))
    if not artifact_type or not reference:
        return "unavailable"
    if not bool(getattr(snapshot, "exists", True)):
        return "missing"

    path = Path(reference)
    if not path.is_file():
        return "missing"
    encoding = metadata.get("encoding", "utf-8")
    try:
        content = path.read_text(encoding=encoding)
    except (OSError, UnicodeError):
        return "missing"

    from agent.checkpoint.recorder import fact_digest

    if fact_digest(content) != snapshot.digest:
        return "mismatched"
    context.set_artifact(
        Artifact(
            id=snapshot.artifact_id,
            type=artifact_type,
            content=content,
            summary=content[:200],
            storage_uri=reference,
            metadata=metadata,
            created_by=metadata.get("producer_stage_id", ""),
        )
    )
    return "hydrated"


def hydrate_checkpoint_artifacts(
    snapshots: Iterable[object],
    context: ExecutionContext,
) -> ArtifactHydrationReport:
    """Hydrate checkpoint ``ArtifactSnapshot`` values into an ExecutionContext."""
    hydrated: list[str] = []
    missing: list[str] = []
    mismatched: list[str] = []
    unavailable: list[str] = []
    for snapshot in snapshots:
        outcome = _hydrate_snapshot(snapshot, context)
        artifact_type = str(getattr(snapshot, "artifact_type", ""))
        if outcome == "hydrated":
            hydrated.append(artifact_type)
        elif outcome == "missing":
            missing.append(artifact_type)
        elif outcome == "mismatched":
            mismatched.append(artifact_type)
        else:
            unavailable.append(artifact_type)
    return ArtifactHydrationReport(
        hydrated_types=tuple(dict.fromkeys(hydrated)),
        missing_types=tuple(dict.fromkeys(missing)),
        mismatched_types=tuple(dict.fromkeys(mismatched)),
        unavailable_types=tuple(dict.fromkeys(unavailable)),
    )


def hydrate_run_artifacts(
    artifacts: Iterable[object],
    context: ExecutionContext,
) -> ArtifactHydrationReport:
    """Hydrate Run-level Artifact facts that carry file references."""
    from agent.checkpoint.contracts import ArtifactSnapshot

    snapshots = []
    for artifact in artifacts:
        snapshots.append(
            ArtifactSnapshot(
                artifact_id=str(getattr(artifact, "artifact_id", "")),
                artifact_type=str(getattr(artifact, "artifact_type", "")),
                digest=str(getattr(artifact, "digest", "")),
                exists=bool(getattr(artifact, "exists", True)),
                reference=str(getattr(artifact, "reference", "")),
                metadata=(
                    ("encoding", str(getattr(artifact, "encoding", "utf-8"))),
                    ("producer_stage_id", str(getattr(artifact, "producer_stage_id", ""))),
                ),
            )
        )
    return hydrate_checkpoint_artifacts(snapshots, context)


def hydrate_declared_file_inputs(
    stage: object,
    snapshots: Iterable[object],
    context: ExecutionContext,
) -> ArtifactHydrationReport:
    """Hydrate an artifact input from the stage's declared file binding.

    This is deliberately based on the stage's explicit ``path`` and
    ``artifact`` bindings.  It does not infer filenames from artifact names.
    It covers the crash window where a write side effect exists but the
    producer artifact had no durable reference of its own yet.
    """
    arguments = getattr(stage, "arguments", ()) or ()
    path = next(
        (
            str(getattr(argument, "constant"))
            for argument in arguments
            if getattr(argument, "param", "") == "path"
            and getattr(argument, "constant", None) is not None
        ),
        "",
    )
    artifact_names = tuple(
        str(getattr(argument, "artifact"))
        for argument in arguments
        if getattr(argument, "artifact", None)
    )
    if not path or not artifact_names:
        return ArtifactHydrationReport()

    snapshot_map = {
        str(getattr(snapshot, "artifact_type", "")): snapshot
        for snapshot in snapshots
    }
    hydrated: list[str] = []
    missing: list[str] = []
    mismatched: list[str] = []
    unavailable: list[str] = []
    for artifact_name in artifact_names:
        if context.get_artifact(artifact_name) is not None:
            continue
        snapshot = snapshot_map.get(artifact_name)
        if snapshot is None:
            unavailable.append(artifact_name)
            continue
        outcome = _hydrate_snapshot(
            snapshot,
            context,
            reference_override=path,
        )
        if outcome == "hydrated":
            hydrated.append(artifact_name)
        elif outcome == "missing":
            missing.append(artifact_name)
        elif outcome == "mismatched":
            mismatched.append(artifact_name)
        else:
            unavailable.append(artifact_name)
    return ArtifactHydrationReport(
        hydrated_types=tuple(dict.fromkeys(hydrated)),
        missing_types=tuple(dict.fromkeys(missing)),
        mismatched_types=tuple(dict.fromkeys(mismatched)),
        unavailable_types=tuple(dict.fromkeys(unavailable)),
    )


__all__ = [
    "ArtifactHydrationReport",
    "hydrate_checkpoint_artifacts",
    "hydrate_declared_file_inputs",
    "hydrate_run_artifacts",
]
