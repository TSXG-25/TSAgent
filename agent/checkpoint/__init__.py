"""Public v2.2B checkpoint exports with demand-driven module loading.

The durable SQLite bootstrap only needs the checkpoint codec and contracts.
Keeping this namespace lazy prevents importing the Workflow/Task execution
graph merely because a caller imports ``agent.checkpoint.codec``.
"""

from __future__ import annotations

import importlib
from typing import Any


_EXPORTS: dict[str, tuple[str, str]] = {
    "ALLOWED_TRANSITIONS": ("lifecycle", "ALLOWED_TRANSITIONS"),
    "ArtifactSnapshot": ("contracts", "ArtifactSnapshot"),
    "CheckpointCodecError": ("codec", "CheckpointCodecError"),
    "CheckpointRecorder": ("recorder", "CheckpointRecorder"),
    "CheckpointStatus": ("reason_codes", "CheckpointStatus"),
    "CheckpointStore": ("store", "CheckpointStore"),
    "CheckpointStoreError": ("store", "CheckpointStoreError"),
    "CompatibilityAssessment": ("compatibility", "CompatibilityAssessment"),
    "CompatibilityRegistry": ("compatibility", "CompatibilityRegistry"),
    "ExternalStateGuard": ("contracts", "ExternalStateGuard"),
    "FailureEventSnapshot": ("contracts", "FailureEventSnapshot"),
    "GuardStatus": ("reason_codes", "GuardStatus"),
    "InvalidCheckpointTransition": ("lifecycle", "InvalidCheckpointTransition"),
    "InMemoryCheckpointStore": ("store", "InMemoryCheckpointStore"),
    "PendingTarget": ("projection", "PendingTarget"),
    "ResumeAction": ("reason_codes", "ResumeAction"),
    "ResumeContext": ("contracts", "ResumeContext"),
    "ResumeDecision": ("contracts", "ResumeDecision"),
    "ResumeDisposition": ("reason_codes", "ResumeDisposition"),
    "ResumeReasonCode": ("reason_codes", "ResumeReasonCode"),
    "RunCheckpoint": ("contracts", "RunCheckpoint"),
    "RuntimeEvidence": ("contracts", "RuntimeEvidence"),
    "SideEffectState": ("reason_codes", "SideEffectState"),
    "TaskEffectRecord": ("contracts", "TaskEffectRecord"),
    "WorkflowCheckpointRequest": ("recorder", "WorkflowCheckpointRequest"),
    "WorkflowMigration": ("compatibility", "WorkflowMigration"),
    "advance_checkpoint": ("lifecycle", "advance_checkpoint"),
    "allowed_transition": ("lifecycle", "allowed_transition"),
    "append_checkpoint": ("lifecycle", "append_checkpoint"),
    "assess_compatibility": ("compatibility", "assess_compatibility"),
    "checkpoint_digest": ("codec", "checkpoint_digest"),
    "checkpoint_result_metadata": ("recorder", "checkpoint_result_metadata"),
    "deserialize_checkpoint": ("codec", "deserialize_checkpoint"),
    "effect_state_for_task": ("recorder", "effect_state_for_task"),
    "fact_digest": ("recorder", "fact_digest"),
    "failure_snapshot": ("recorder", "failure_snapshot"),
    "json_fact": ("recorder", "json_fact"),
    "lifecycle_contract": ("lifecycle", "lifecycle_contract"),
    "major_version": ("compatibility", "major_version"),
    "new_checkpoint_id": ("recorder", "new_checkpoint_id"),
    "project_pending_target": ("projection", "project_pending_target"),
    "serialize_checkpoint": ("codec", "serialize_checkpoint"),
    "snapshot_artifacts": ("recorder", "snapshot_artifacts"),
    "utc_timestamp": ("recorder", "utc_timestamp"),
    "validate_resume": ("validator", "validate_resume"),
    "validate_transition": ("lifecycle", "validate_transition"),
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    module_name, attribute = target
    value = getattr(importlib.import_module(f".{module_name}", __name__), attribute)
    globals()[name] = value
    return value
