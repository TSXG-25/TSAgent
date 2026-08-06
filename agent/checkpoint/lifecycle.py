"""Explicit Checkpoint lifecycle transitions (ADR-0016)."""
from __future__ import annotations

from dataclasses import replace
from typing import Mapping

from .contracts import RunCheckpoint
from .reason_codes import CheckpointStatus


ALLOWED_TRANSITIONS: Mapping[CheckpointStatus, frozenset[CheckpointStatus]] = {
    CheckpointStatus.CREATED: frozenset({
        CheckpointStatus.RUNNING,
        CheckpointStatus.CANCELLED,
    }),
    CheckpointStatus.RUNNING: frozenset({
        CheckpointStatus.SUSPENDED,
        CheckpointStatus.WAITING_USER,
        CheckpointStatus.FAILED_RECOVERABLE,
        CheckpointStatus.FAILED_TERMINAL,
        CheckpointStatus.COMPLETED,
    }),
    CheckpointStatus.SUSPENDED: frozenset({
        CheckpointStatus.RUNNING,
        CheckpointStatus.CANCELLED,
    }),
    CheckpointStatus.WAITING_USER: frozenset({
        CheckpointStatus.SUSPENDED,
        CheckpointStatus.CANCELLED,
    }),
    CheckpointStatus.FAILED_RECOVERABLE: frozenset({
        CheckpointStatus.SUSPENDED,
        CheckpointStatus.CANCELLED,
    }),
    CheckpointStatus.FAILED_TERMINAL: frozenset(),
    CheckpointStatus.COMPLETED: frozenset(),
    CheckpointStatus.CANCELLED: frozenset(),
}


class InvalidCheckpointTransition(ValueError):
    """Raised when a checkpoint attempts an undeclared lifecycle transition."""


def allowed_transition(
    current: CheckpointStatus | str,
    target: CheckpointStatus | str,
) -> bool:
    current = CheckpointStatus(current)
    target = CheckpointStatus(target)
    return target in ALLOWED_TRANSITIONS[current]


def validate_transition(
    current: CheckpointStatus | str,
    target: CheckpointStatus | str,
) -> None:
    current = CheckpointStatus(current)
    target = CheckpointStatus(target)
    if not allowed_transition(current, target):
        raise InvalidCheckpointTransition(f"非法 Checkpoint 状态迁移: {current.value} → {target.value}")


def advance_checkpoint(
    checkpoint: RunCheckpoint,
    target_status: CheckpointStatus | str,
    *,
    checkpoint_id: str,
    updated_at: str,
    **changes,
) -> RunCheckpoint:
    """Create a new immutable checkpoint instead of mutating history."""
    validate_transition(checkpoint.status, target_status)
    protected = {
        "run_id", "session_id", "conversation_id", "user_scope",
        "workflow_id", "workflow_version", "plan_version",
        "checkpoint_id", "parent_checkpoint_id", "sequence_number",
        "status", "updated_at",
    }
    changed_protected = protected.intersection(changes)
    if changed_protected:
        raise ValueError(
            "Checkpoint 链身份/版本字段不可由 advance_checkpoint 改写: "
            + ", ".join(sorted(changed_protected))
        )
    if not checkpoint_id.strip():
        raise ValueError("新的 checkpoint_id 不能为空")
    if checkpoint_id == checkpoint.checkpoint_id:
        raise ValueError("新 checkpoint_id 必须与 parent 不同")
    values = dict(changes)
    values.update({
        "checkpoint_id": checkpoint_id,
        "parent_checkpoint_id": checkpoint.checkpoint_id,
        "sequence_number": checkpoint.sequence_number + 1,
        "status": CheckpointStatus(target_status),
        "updated_at": updated_at,
    })
    return replace(checkpoint, **values)


def append_checkpoint(
    checkpoint: RunCheckpoint,
    *,
    checkpoint_id: str,
    updated_at: str,
    status: CheckpointStatus | str | None = None,
    **changes,
) -> RunCheckpoint:
    """Append a fact snapshot, allowing the lifecycle status to stay unchanged.

    A Stage completing while a Workflow remains ``RUNNING`` is a real state
    change even though the lifecycle status does not change.  v2.2A's
    transition table still governs status changes; this helper only adds the
    immutable child-snapshot operation needed by v2.2B progress recording.
    """
    target_status = CheckpointStatus(status or checkpoint.status)
    if target_status is not checkpoint.status:
        validate_transition(checkpoint.status, target_status)
    protected = {
        "run_id", "session_id", "conversation_id", "user_scope",
        "workflow_id", "workflow_version", "plan_version",
        "checkpoint_id", "parent_checkpoint_id", "sequence_number",
        "updated_at",
    }
    changed_protected = protected.intersection(changes)
    if changed_protected:
        raise ValueError(
            "Checkpoint 链身份/版本字段不可由 append_checkpoint 改写: "
            + ", ".join(sorted(changed_protected))
        )
    if not checkpoint_id.strip():
        raise ValueError("新的 checkpoint_id 不能为空")
    if checkpoint_id == checkpoint.checkpoint_id:
        raise ValueError("新 checkpoint_id 必须与 parent 不同")
    values = dict(changes)
    values.update({
        "checkpoint_id": checkpoint_id,
        "parent_checkpoint_id": checkpoint.checkpoint_id,
        "sequence_number": checkpoint.sequence_number + 1,
        "status": target_status,
        "updated_at": updated_at,
    })
    return replace(checkpoint, **values)


def lifecycle_contract() -> dict[str, list[str]]:
    """Serializable transition table used by the benchmark oracle."""
    return {
        current.value: sorted(status.value for status in targets)
        for current, targets in ALLOWED_TRANSITIONS.items()
    }


__all__ = [
    "ALLOWED_TRANSITIONS",
    "InvalidCheckpointTransition",
    "append_checkpoint",
    "advance_checkpoint",
    "allowed_transition",
    "lifecycle_contract",
    "validate_transition",
]
