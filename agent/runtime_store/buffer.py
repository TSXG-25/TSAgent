"""Process-local Checkpoint staging used before durable finalization.

This is not a persistence implementation and is intentionally named as a
staging buffer rather than an ``InMemoryCheckpointStore``.  A Workflow may
produce several immutable Checkpoint facts while its external effect is in
flight; the buffer keeps those facts until one Finalization Bundle publishes
the complete chain atomically to SQLite.
"""

from __future__ import annotations

from agent.checkpoint.codec import checkpoint_digest
from agent.checkpoint.contracts import RunCheckpoint
from agent.checkpoint.store import CheckpointStoreError


class CheckpointStagingBuffer:
    """Append-only process-local chain with no durable write side effect."""

    def __init__(self) -> None:
        self._checkpoints: dict[str, RunCheckpoint] = {}
        self._run_history: dict[str, list[str]] = {}
        self._workflow_history: dict[tuple[str, str], list[str]] = {}

    def save(self, checkpoint: RunCheckpoint) -> RunCheckpoint:
        existing = self._checkpoints.get(checkpoint.checkpoint_id)
        if existing is not None:
            if checkpoint_digest(existing) != checkpoint_digest(checkpoint):
                raise CheckpointStoreError(
                    f"Checkpoint identity collision: {checkpoint.checkpoint_id}"
                )
            return existing

        history = self._workflow_history.setdefault(
            (checkpoint.run_id, checkpoint.workflow_id), []
        )
        if history:
            latest = self._checkpoints[history[-1]]
            if checkpoint.parent_checkpoint_id != latest.checkpoint_id:
                raise CheckpointStoreError(
                    "staged Checkpoint parent must extend the latest Workflow fact"
                )
            if checkpoint.sequence_number != latest.sequence_number + 1:
                raise CheckpointStoreError(
                    "staged Checkpoint sequence must increment by one"
                )
        elif checkpoint.sequence_number != 0 or checkpoint.parent_checkpoint_id is not None:
            raise CheckpointStoreError(
                "first staged Checkpoint must have sequence=0 and no parent"
            )

        self._checkpoints[checkpoint.checkpoint_id] = checkpoint
        history.append(checkpoint.checkpoint_id)
        self._run_history.setdefault(checkpoint.run_id, []).append(
            checkpoint.checkpoint_id
        )
        return checkpoint

    def get(self, checkpoint_id: str) -> RunCheckpoint | None:
        return self._checkpoints.get(checkpoint_id)

    def latest(self, run_id: str) -> RunCheckpoint | None:
        history = self._run_history.get(run_id, [])
        return self._checkpoints[history[-1]] if history else None

    def latest_for_workflow(
        self,
        run_id: str,
        workflow_id: str,
        *,
        activation_attempt_id: str = "",
    ) -> RunCheckpoint | None:
        history = self._workflow_history.get((run_id, workflow_id), [])
        for checkpoint_id in reversed(history):
            checkpoint = self._checkpoints[checkpoint_id]
            if (
                not activation_attempt_id
                or checkpoint.activation_attempt_id == activation_attempt_id
            ):
                return checkpoint
        return None

    def history(self, run_id: str) -> tuple[RunCheckpoint, ...]:
        return tuple(
            self._checkpoints[checkpoint_id]
            for checkpoint_id in self._run_history.get(run_id, [])
        )


__all__ = ["CheckpointStagingBuffer"]
