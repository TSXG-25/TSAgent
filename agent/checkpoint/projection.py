"""Lossy one-way projections from Checkpoint to Conversation Runtime."""
from __future__ import annotations

from dataclasses import dataclass

from .contracts import RunCheckpoint
from .reason_codes import CheckpointStatus


@dataclass(frozen=True)
class PendingTarget:
    """Small conversational projection; never enough to reconstruct a Run."""

    run_id: str
    workflow_id: str
    target_summary: str
    active_stage_summary: str
    status: CheckpointStatus
    last_updated_at: str

    def to_dict(self) -> dict[str, str]:
        return {
            "run_id": self.run_id,
            "workflow_id": self.workflow_id,
            "target_summary": self.target_summary,
            "active_stage_summary": self.active_stage_summary,
            "status": self.status.value,
            "last_updated_at": self.last_updated_at,
        }


def project_pending_target(checkpoint: RunCheckpoint) -> PendingTarget | None:
    """Project only the fields Conversation needs for target disambiguation."""
    if checkpoint.status in {
        CheckpointStatus.COMPLETED,
        CheckpointStatus.CANCELLED,
        CheckpointStatus.FAILED_TERMINAL,
    }:
        return None
    if not checkpoint.target_summary and not checkpoint.active_stage_id:
        return None
    return PendingTarget(
        run_id=checkpoint.run_id,
        workflow_id=checkpoint.workflow_id,
        target_summary=checkpoint.target_summary,
        active_stage_summary=checkpoint.active_stage_id,
        status=checkpoint.status,
        last_updated_at=checkpoint.updated_at,
    )


__all__ = ["PendingTarget", "project_pending_target"]
