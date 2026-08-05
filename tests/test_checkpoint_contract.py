"""v2.2A Run Checkpoint Contract tests (ADR-0016)."""
from dataclasses import FrozenInstanceError

import pytest

from agent.checkpoint import (
    CheckpointStatus,
    CompatibilityRegistry,
    ExternalStateGuard,
    GuardStatus,
    InvalidCheckpointTransition,
    ResumeAction,
    ResumeContext,
    ResumeDisposition,
    ResumeDecision,
    ResumeReasonCode,
    RunCheckpoint,
    SideEffectState,
    TaskEffectRecord,
    WorkflowMigration,
    advance_checkpoint,
    checkpoint_digest,
    deserialize_checkpoint,
    project_pending_target,
    serialize_checkpoint,
    validate_resume,
)
from benchmarks.checkpoint.cases import build_cases, build_registry
from benchmarks.checkpoint.metadata import benchmark_metadata


def _checkpoint(**updates):
    base = {
        "run_id": "run-test",
        "checkpoint_id": "cp-test",
        "parent_checkpoint_id": None,
        "sequence_number": 0,
        "session_id": "session-test",
        "conversation_id": "conversation-test",
        "user_scope": "user-test",
        "workflow_id": "workflow.test",
        "workflow_version": "1.0.0",
        "plan_version": "1.0.0",
        "active_stage_id": "stage.test",
        "active_task_id": "task.test",
        "status": CheckpointStatus.SUSPENDED,
        "execution_plan": {"steps": [{"tool": "filesystem.write"}]},
        "target_summary": "output/test.txt",
        "verifier_status": "VERIFIED",
        "checkpoint_schema_version": "1.0",
        "contract_version": "v2.2A",
    }
    base.update(updates)
    return RunCheckpoint(**base)


def _context(**updates):
    base = {
        "workflow_id": "workflow.test",
        "workflow_version": "1.0.0",
        "plan_version": "1.0.0",
        "requested_target": "output/test.txt",
    }
    base.update(updates)
    return ResumeContext(**base)


def test_checkpoint_is_deeply_immutable():
    checkpoint = _checkpoint(execution_plan={"nested": {"items": [1, 2]}})
    with pytest.raises(FrozenInstanceError):
        checkpoint.status = CheckpointStatus.RUNNING  # type: ignore[misc]
    with pytest.raises(TypeError):
        checkpoint.execution_plan["nested"] = {}  # type: ignore[index]
    with pytest.raises(TypeError):
        checkpoint.execution_plan["nested"]["items"] = ()  # type: ignore[index]


def test_state_change_creates_child_checkpoint_and_preserves_parent():
    parent = _checkpoint()
    child = advance_checkpoint(
        parent,
        CheckpointStatus.RUNNING,
        checkpoint_id="cp-child",
        updated_at="2026-08-05T00:02:00Z",
    )
    assert parent.status is CheckpointStatus.SUSPENDED
    assert child.status is CheckpointStatus.RUNNING
    assert child.parent_checkpoint_id == parent.checkpoint_id
    assert child.sequence_number == 1
    with pytest.raises(InvalidCheckpointTransition):
        advance_checkpoint(
            _checkpoint(status=CheckpointStatus.COMPLETED),
            CheckpointStatus.RUNNING,
            checkpoint_id="cp-invalid",
            updated_at="2026-08-05T00:02:00Z",
        )
    with pytest.raises(ValueError):
        advance_checkpoint(
            parent,
            CheckpointStatus.RUNNING,
            checkpoint_id="cp-child-2",
            updated_at="2026-08-05T00:02:00Z",
            workflow_version="2.0.0",
        )


def test_resume_disposition_and_action_are_separate():
    with pytest.raises(ValueError):
        # REQUIRE_CLARIFICATION cannot carry an action.
        ResumeDecision(
            disposition=ResumeDisposition.REQUIRE_CLARIFICATION,
            action=ResumeAction.RESUME_EXACT,
            run_id="r",
            checkpoint_id="c",
            resume_stage_id=None,
            resume_task_id=None,
            resulting_status=CheckpointStatus.WAITING_USER,
            reason_code=ResumeReasonCode.TARGET_CONFLICT,
            clarification_question="which one?",
        )


def test_codec_is_deterministic_and_preserves_validator_decision():
    checkpoint = _checkpoint(
        task_effect_records=(TaskEffectRecord(
            task_id="task.previous",
            effect_state=SideEffectState.COMMITTED,
            idempotency_key="effect-previous",
        ),),
        idempotency_keys=("effect-previous",),
        completed_task_ids=("task.previous",),
    )
    payload = serialize_checkpoint(checkpoint)
    restored = deserialize_checkpoint(payload)
    assert payload == serialize_checkpoint(restored)
    assert checkpoint_digest(checkpoint) == checkpoint_digest(restored)
    context = _context()
    assert validate_resume(checkpoint, context).to_dict() == validate_resume(
        restored, context
    ).to_dict()


def test_unknown_side_effect_requires_clarification_without_action():
    checkpoint = _checkpoint(task_effect_records=(TaskEffectRecord(
        task_id="task.test",
        effect_state=SideEffectState.UNKNOWN,
    ),))
    decision = validate_resume(checkpoint, _context())
    assert decision.disposition is ResumeDisposition.REQUIRE_CLARIFICATION
    assert decision.action is None
    assert decision.resulting_status is CheckpointStatus.WAITING_USER
    assert decision.reason_code is ResumeReasonCode.UNKNOWN_SIDE_EFFECT


def test_replay_blocks_committed_effect_even_when_stage_is_idempotent():
    checkpoint = _checkpoint(task_effect_records=(TaskEffectRecord(
        task_id="task.test",
        effect_state=SideEffectState.COMMITTED,
    ),))
    decision = validate_resume(
        checkpoint,
        _context(
            requested_action=ResumeAction.REPLAY_FROM_STAGE,
            stage_idempotent=True,
        ),
    )
    assert decision.disposition is ResumeDisposition.REJECT
    assert decision.action is None
    assert decision.reason_code is ResumeReasonCode.DUPLICATE_SIDE_EFFECT


def test_committed_active_task_without_completion_boundary_requires_clarification():
    checkpoint = _checkpoint(task_effect_records=(TaskEffectRecord(
        task_id="task.test",
        effect_state=SideEffectState.COMMITTED,
    ),))
    decision = validate_resume(checkpoint, _context())
    assert decision.disposition is ResumeDisposition.REQUIRE_CLARIFICATION
    assert decision.reason_code is ResumeReasonCode.DUPLICATE_SIDE_EFFECT


def test_plan_change_forces_replan_and_workflow_migration_is_explicit():
    checkpoint = _checkpoint()
    decision = validate_resume(checkpoint, _context(plan_version="2.0.0"))
    assert decision.disposition is ResumeDisposition.ALLOW
    assert decision.action is ResumeAction.REPLAN_FROM_CHECKPOINT

    migrated = validate_resume(
        checkpoint,
        _context(workflow_version="2.0.0"),
        compatibility_registry=CompatibilityRegistry(
            workflow_migrations=(
                # The mapping is the only thing that permits cross-version Replan.
                WorkflowMigration(
                    workflow_id="workflow.test",
                    from_version="1.0.0",
                    to_version="2.0.0",
                    migration_id="test-migration",
                ),
            ),
        ),
    )
    assert migrated.action is ResumeAction.REPLAN_FROM_CHECKPOINT
    assert migrated.reason_code is ResumeReasonCode.ALLOWED_REPLAN


def test_external_guard_mismatch_rejects_without_querying_external_world():
    decision = validate_resume(
        _checkpoint(),
        _context(external_state_evidence=(ExternalStateGuard(
            resource_id="output/test.txt",
            guard_type="FILE_CONTENT_HASH",
            expected_value="old",
            observed_value="new",
            status=GuardStatus.MISMATCH,
        ),)),
    )
    assert decision.disposition is ResumeDisposition.REJECT
    assert decision.reason_code is ResumeReasonCode.EXTERNAL_STATE_MISMATCH


def test_historical_guard_requires_fresh_current_evidence():
    checkpoint = _checkpoint(external_state_guards=(ExternalStateGuard(
        resource_id="output/test.txt",
        guard_type="FILE_CONTENT_HASH",
        expected_value="sha256:old",
    ),))
    decision = validate_resume(checkpoint, _context())
    assert decision.disposition is ResumeDisposition.REQUIRE_CLARIFICATION
    assert decision.reason_code is ResumeReasonCode.EXTERNAL_STATE_UNKNOWN


def test_pending_target_is_one_way_and_lossy():
    pending = project_pending_target(_checkpoint())
    assert pending is not None
    assert set(pending.to_dict()) == {
        "run_id", "workflow_id", "target_summary",
        "active_stage_summary", "status", "last_updated_at",
    }
    assert "execution_plan" not in pending.to_dict()
    assert not hasattr(type(pending), "from_pending_target")
    assert project_pending_target(_checkpoint(status=CheckpointStatus.COMPLETED)) is None


def test_checkpoint_dataset_oracle_and_metadata_are_stable():
    cases = build_cases()
    assert len(cases) == 16
    assert len({case.id for case in cases}) == len(cases)
    metadata = benchmark_metadata(cases)
    assert metadata["benchmark_name"] == "run-checkpoint-v2.2a"
    assert len(metadata["dataset_hash"]) == 64
    # The dataset validator is the executable oracle; this keeps the test
    # independent of a real Workflow/Executor environment.
    for case in cases:
        checkpoint = RunCheckpoint.from_dict(case.checkpoint)
        context = ResumeContext.from_dict(case.context)
        decision = validate_resume(
            checkpoint,
            context,
            compatibility_registry=build_registry(case.registry),
        )
        assert decision.to_dict()["disposition"] == case.expected_disposition
