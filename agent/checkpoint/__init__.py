"""v2.2A Run Checkpoint Contract (ADR-0016).

This package is a fact/validation boundary only.  It does not create a
Workflow runtime, execute tools, or rebuild a checkpoint from a conversation
projection.
"""

from .codec import (
    CheckpointCodecError,
    checkpoint_digest,
    deserialize_checkpoint,
    serialize_checkpoint,
)
from .compatibility import (
    CompatibilityAssessment,
    CompatibilityRegistry,
    WorkflowMigration,
    assess_compatibility,
    major_version,
)
from .contracts import (
    ArtifactSnapshot,
    ExternalStateGuard,
    FailureEventSnapshot,
    ResumeContext,
    ResumeDecision,
    RunCheckpoint,
    RuntimeEvidence,
    TaskEffectRecord,
)
from .lifecycle import (
    ALLOWED_TRANSITIONS,
    InvalidCheckpointTransition,
    advance_checkpoint,
    allowed_transition,
    lifecycle_contract,
    validate_transition,
)
from .projection import PendingTarget, project_pending_target
from .reason_codes import (
    CheckpointStatus,
    GuardStatus,
    ResumeAction,
    ResumeDisposition,
    ResumeReasonCode,
    SideEffectState,
)
from .validator import validate_resume

__all__ = [
    "ALLOWED_TRANSITIONS",
    "ArtifactSnapshot",
    "CheckpointCodecError",
    "CheckpointStatus",
    "CompatibilityAssessment",
    "CompatibilityRegistry",
    "ExternalStateGuard",
    "FailureEventSnapshot",
    "GuardStatus",
    "InvalidCheckpointTransition",
    "PendingTarget",
    "ResumeAction",
    "ResumeContext",
    "ResumeDecision",
    "ResumeDisposition",
    "ResumeReasonCode",
    "RunCheckpoint",
    "RuntimeEvidence",
    "SideEffectState",
    "TaskEffectRecord",
    "WorkflowMigration",
    "advance_checkpoint",
    "allowed_transition",
    "assess_compatibility",
    "checkpoint_digest",
    "deserialize_checkpoint",
    "lifecycle_contract",
    "major_version",
    "project_pending_target",
    "serialize_checkpoint",
    "validate_resume",
    "validate_transition",
]
