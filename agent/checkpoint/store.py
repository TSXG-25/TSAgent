"""Append-only Checkpoint storage boundary for v2.2B.

The first Workflow integration uses an in-memory implementation so the
runtime contract can be verified without introducing a database or a new
orchestration service.  A durable implementation can satisfy the same
protocol later.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from .codec import checkpoint_digest
from .contracts import RunCheckpoint


class CheckpointStoreError(ValueError):
    """Raised when a Checkpoint chain would become inconsistent."""


@runtime_checkable
class CheckpointStore(Protocol):
    """Minimal append-only store consumed by the Workflow runtime."""

    def save(self, checkpoint: RunCheckpoint) -> RunCheckpoint:
        """Persist one immutable Checkpoint and return the stored value."""

    def get(self, checkpoint_id: str) -> RunCheckpoint | None:
        """Return a Checkpoint by identity."""

    def latest(self, run_id: str) -> RunCheckpoint | None:
        """Return the latest Checkpoint in a Run."""

    def history(self, run_id: str) -> tuple[RunCheckpoint, ...]:
        """Return the complete ordered Checkpoint chain for a Run."""


class InMemoryCheckpointStore:
    """Strict append-only store used by v2.2B runtime and tests."""

    def __init__(self) -> None:
        self._checkpoints: dict[str, RunCheckpoint] = {}
        self._run_history: dict[str, list[str]] = {}

    def save(self, checkpoint: RunCheckpoint) -> RunCheckpoint:
        existing = self._checkpoints.get(checkpoint.checkpoint_id)
        if existing is not None:
            if checkpoint_digest(existing) != checkpoint_digest(checkpoint):
                raise CheckpointStoreError(
                    f"Checkpoint identity collision: {checkpoint.checkpoint_id}"
                )
            return existing

        history = self._run_history.setdefault(checkpoint.run_id, [])
        if not history:
            if checkpoint.sequence_number != 0 or checkpoint.parent_checkpoint_id is not None:
                raise CheckpointStoreError("Run 的第一个 Checkpoint 必须是 sequence=0 且无 parent")
        else:
            latest = self._checkpoints[history[-1]]
            if checkpoint.parent_checkpoint_id != latest.checkpoint_id:
                raise CheckpointStoreError(
                    "Checkpoint parent 必须指向该 Run 的 latest Checkpoint"
                )
            if checkpoint.sequence_number != latest.sequence_number + 1:
                raise CheckpointStoreError(
                    "Checkpoint sequence 必须在 latest 基础上递增 1"
                )

        self._checkpoints[checkpoint.checkpoint_id] = checkpoint
        history.append(checkpoint.checkpoint_id)
        return checkpoint

    def get(self, checkpoint_id: str) -> RunCheckpoint | None:
        return self._checkpoints.get(checkpoint_id)

    def latest(self, run_id: str) -> RunCheckpoint | None:
        history = self._run_history.get(run_id, [])
        if not history:
            return None
        return self._checkpoints[history[-1]]

    def history(self, run_id: str) -> tuple[RunCheckpoint, ...]:
        return tuple(
            self._checkpoints[checkpoint_id]
            for checkpoint_id in self._run_history.get(run_id, [])
        )


__all__ = ["CheckpointStore", "CheckpointStoreError", "InMemoryCheckpointStore"]
