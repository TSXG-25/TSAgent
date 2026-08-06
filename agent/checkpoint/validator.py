"""Pure deterministic ResumeValidator for v2.2A.

The validator consumes a checkpoint plus already-collected current facts.  It
never reads a file, calls a tool, queries a database, or asks an LLM.
"""
from __future__ import annotations

from typing import Iterable

from .compatibility import CompatibilityRegistry, assess_compatibility
from .contracts import (
    ExternalStateGuard,
    ResumeContext,
    ResumeDecision,
    RunCheckpoint,
    RuntimeEvidence,
)
from .reason_codes import (
    CheckpointStatus,
    GuardStatus,
    ResumeAction,
    ResumeDisposition,
    ResumeReasonCode,
    SideEffectState,
)


_TERMINAL = frozenset({
    CheckpointStatus.COMPLETED,
    CheckpointStatus.CANCELLED,
    CheckpointStatus.FAILED_TERMINAL,
})
_UNKNOWN_EFFECTS = frozenset({
    SideEffectState.UNKNOWN,
    SideEffectState.STARTED,
    SideEffectState.FAILED_AFTER_COMMIT,
})
_REPLAY_BLOCKING_EFFECTS = frozenset({
    SideEffectState.COMMITTED,
    SideEffectState.STARTED,
    SideEffectState.FAILED_AFTER_COMMIT,
    SideEffectState.UNKNOWN,
})


def _target_key(value: str) -> str:
    return str(value or "").strip().replace("\\", "/").casefold().rstrip("/")


def _evidence(
    kind: str,
    *,
    expected: str = "",
    observed: str = "",
    status: str = "VERIFIED",
    detail: str = "",
) -> RuntimeEvidence:
    return RuntimeEvidence(
        source="resume_validator",
        kind=kind,
        expected=expected,
        observed=observed,
        status=status,
        detail=detail,
    )


def _make_decision(
    checkpoint: RunCheckpoint,
    *,
    disposition: ResumeDisposition,
    action: ResumeAction | None,
    reason: ResumeReasonCode,
    evidence: Iterable[RuntimeEvidence] = (),
    stage_id: str | None = None,
    task_id: str | None = None,
    question: str | None = None,
) -> ResumeDecision:
    if disposition is ResumeDisposition.ALLOW:
        resulting_status = (
            CheckpointStatus.CANCELLED
            if action is ResumeAction.ABANDON_AND_RESTART
            else CheckpointStatus.RUNNING
        )
    elif disposition is ResumeDisposition.REQUIRE_CLARIFICATION:
        resulting_status = CheckpointStatus.WAITING_USER
    else:
        # REJECT does not mutate the historical checkpoint.
        resulting_status = checkpoint.status
    return ResumeDecision(
        disposition=disposition,
        action=action,
        run_id=checkpoint.run_id,
        checkpoint_id=checkpoint.checkpoint_id,
        resume_stage_id=stage_id,
        resume_task_id=task_id,
        resulting_status=resulting_status,
        reason_code=reason,
        evidence=tuple(evidence),
        clarification_question=question,
    )


def _clarify(
    checkpoint: RunCheckpoint,
    reason: ResumeReasonCode,
    evidence: Iterable[RuntimeEvidence],
    question: str,
) -> ResumeDecision:
    return _make_decision(
        checkpoint,
        disposition=ResumeDisposition.REQUIRE_CLARIFICATION,
        action=None,
        reason=reason,
        evidence=evidence,
        question=question,
    )


def _reject(
    checkpoint: RunCheckpoint,
    reason: ResumeReasonCode,
    evidence: Iterable[RuntimeEvidence],
) -> ResumeDecision:
    return _make_decision(
        checkpoint,
        disposition=ResumeDisposition.REJECT,
        action=None,
        reason=reason,
        evidence=evidence,
    )


def validate_resume(
    checkpoint: RunCheckpoint,
    current_context: ResumeContext,
    external_state_evidence: Iterable[ExternalStateGuard] | None = None,
    compatibility_registry: CompatibilityRegistry | None = None,
) -> ResumeDecision:
    """Return a deterministic decision from a checkpoint and current facts.

    The function is deliberately conservative.  Ambiguity, unknown side
    effects, and unverified external state never become an implicit action.
    """
    registry = compatibility_registry or CompatibilityRegistry()
    evidence: list[RuntimeEvidence] = list(checkpoint.runtime_evidence)

    if not current_context.workflow_id or not current_context.workflow_version or not current_context.plan_version:
        evidence.append(_evidence(
            "current_context",
            expected="workflow_id/workflow_version/plan_version",
            observed="missing",
            status="MISSING",
        ))
        return _reject(checkpoint, ResumeReasonCode.MISSING_CONTEXT, evidence)

    candidates = current_context.candidate_run_ids
    if len(candidates) > 1:
        evidence.append(_evidence(
            "run_addressing",
            expected="one candidate run",
            observed=",".join(candidates),
            status="AMBIGUOUS",
        ))
        return _clarify(
            checkpoint,
            ResumeReasonCode.AMBIGUOUS_RUN,
            evidence,
            "存在多个可能的运行记录，请明确要恢复哪一个任务。",
        )
    if candidates and candidates[0] != checkpoint.run_id:
        evidence.append(_evidence(
            "run_addressing",
            expected=checkpoint.run_id,
            observed=candidates[0],
            status="MISMATCH",
        ))
        return _reject(checkpoint, ResumeReasonCode.NO_RUN_MATCH, evidence)

    if (
        current_context.requested_target
        and checkpoint.target_summary
        and _target_key(current_context.requested_target)
        != _target_key(checkpoint.target_summary)
    ):
        evidence.append(_evidence(
            "target_conflict",
            expected=checkpoint.target_summary,
            observed=current_context.requested_target,
            status="CONFLICT",
        ))
        return _clarify(
            checkpoint,
            ResumeReasonCode.TARGET_CONFLICT,
            evidence,
            "当前输入与未完成 Run 的目标不一致，请明确要恢复旧任务还是开始新任务。",
        )

    if checkpoint.status in _TERMINAL:
        evidence.append(_evidence(
            "checkpoint_status",
            expected="non-terminal",
            observed=checkpoint.status.value,
            status="REJECTED",
        ))
        return _reject(checkpoint, ResumeReasonCode.TERMINAL_CHECKPOINT, evidence)

    if checkpoint.invalidation_reasons:
        evidence.append(_evidence(
            "invalidation",
            expected="no invalidation reasons",
            observed=";".join(checkpoint.invalidation_reasons),
            status="INVALID",
        ))
        return _reject(checkpoint, ResumeReasonCode.CHECKPOINT_INVALIDATED, evidence)

    missing_permissions = sorted(
        set(current_context.required_permissions)
        - set(current_context.available_permissions)
    )
    if missing_permissions:
        evidence.append(_evidence(
            "permission_scope",
            expected=",".join(current_context.required_permissions),
            observed=",".join(current_context.available_permissions),
            status="MISSING",
            detail=",".join(missing_permissions),
        ))
        return _reject(checkpoint, ResumeReasonCode.MISSING_PERMISSION, evidence)

    guards = tuple(
        external_state_evidence
        if external_state_evidence is not None
        else current_context.external_state_evidence
    )
    if checkpoint.external_state_guards and not guards:
        evidence.append(_evidence(
            "external_state_guard",
            expected="fresh current guard evidence",
            observed="missing",
            status="MISSING",
        ))
        return _clarify(
            checkpoint,
            ResumeReasonCode.EXTERNAL_STATE_UNKNOWN,
            evidence,
            "Checkpoint 依赖的外部状态尚未重新确认，请先完成状态校验。",
        )
    for guard in guards:
        if guard.status is GuardStatus.MISMATCH:
            evidence.append(_evidence(
                "external_state_guard",
                expected=guard.expected_value,
                observed=guard.observed_value,
                status="MISMATCH",
                detail=f"{guard.guard_type}:{guard.resource_id}",
            ))
            return _reject(checkpoint, ResumeReasonCode.EXTERNAL_STATE_MISMATCH, evidence)
    unknown_guards = [
        guard for guard in guards
        if guard.status in {GuardStatus.UNKNOWN, GuardStatus.MISSING}
    ]
    if unknown_guards:
        evidence.extend(
            _evidence(
                "external_state_guard",
                expected=guard.expected_value,
                observed=guard.observed_value,
                status=guard.status.value,
                detail=f"{guard.guard_type}:{guard.resource_id}",
            )
            for guard in unknown_guards
        )
        return _clarify(
            checkpoint,
            ResumeReasonCode.EXTERNAL_STATE_UNKNOWN,
            evidence,
            "恢复所需的外部状态无法确认，请先确认资源状态后再继续。",
        )

    assessment = assess_compatibility(checkpoint, current_context, registry)
    evidence.extend(assessment.evidence)
    if not assessment.schema_compatible:
        return _reject(checkpoint, ResumeReasonCode.SCHEMA_INCOMPATIBLE, evidence)
    if not assessment.contract_compatible:
        return _reject(checkpoint, ResumeReasonCode.CONTRACT_INCOMPATIBLE, evidence)

    requested = current_context.requested_action
    # Explicit abandon is a user-selected action, but it still cannot revive a
    # terminal checkpoint (handled above) or bypass unknown external facts.
    if requested is ResumeAction.ABANDON_AND_RESTART:
        return _make_decision(
            checkpoint,
            disposition=ResumeDisposition.ALLOW,
            action=requested,
            reason=ResumeReasonCode.ALLOWED_ABANDON,
            evidence=evidence,
        )

    committed_active_task = any(
        record.task_id == checkpoint.active_task_id
        and record.effect_state is SideEffectState.COMMITTED
        and record.task_id not in checkpoint.completed_task_ids
        for record in checkpoint.task_effect_records
    )
    if committed_active_task:
        evidence.append(_evidence(
            "side_effect_completion_boundary",
            expected="committed active task is marked completed",
            observed=checkpoint.active_task_id,
            status="UNKNOWN",
        ))
        if requested is ResumeAction.REPLAY_FROM_STAGE:
            return _reject(checkpoint, ResumeReasonCode.DUPLICATE_SIDE_EFFECT, evidence)
        return _clarify(
            checkpoint,
            ResumeReasonCode.DUPLICATE_SIDE_EFFECT,
            evidence,
            "当前任务的副作用已经提交，但完成边界未确认，请先确认是否应从后续任务继续。",
        )

    if not assessment.workflow_same and not assessment.workflow_migratable:
        return _reject(checkpoint, ResumeReasonCode.WORKFLOW_INCOMPATIBLE, evidence)

    if not checkpoint.active_stage_id and not checkpoint.target_summary:
        return _reject(checkpoint, ResumeReasonCode.NO_RESUMABLE_POSITION, evidence)

    if current_context.requested_stage_id and (
        current_context.requested_stage_id != checkpoint.active_stage_id
    ):
        evidence.append(_evidence(
            "resume_stage",
            expected=checkpoint.active_stage_id,
            observed=current_context.requested_stage_id,
            status="MISMATCH",
        ))
        return _reject(checkpoint, ResumeReasonCode.INVALID_REQUESTED_STAGE, evidence)

    if requested is ResumeAction.REPLAY_FROM_STAGE:
        if not assessment.replay_allowed:
            return _reject(
                checkpoint,
                assessment.reason_code or ResumeReasonCode.PLAN_INCOMPATIBLE,
                evidence,
            )
        if not current_context.stage_idempotent:
            evidence.append(_evidence(
                "stage_idempotency",
                expected="true",
                observed="false",
                status="REJECTED",
            ))
            return _reject(checkpoint, ResumeReasonCode.NON_IDEMPOTENT_STAGE, evidence)
        # Replay starts at the active task.  Committed effects belonging to
        # already-completed tasks are intentionally outside the replay
        # boundary and must not block a safe replay of the current stage.
        replay_blocking = [
            record for record in checkpoint.task_effect_records
            if record.task_id == checkpoint.active_task_id
            and record.effect_state in _REPLAY_BLOCKING_EFFECTS
        ]
        if replay_blocking:
            state_names = ",".join(sorted({record.effect_state.value for record in replay_blocking}))
            evidence.append(_evidence(
                "side_effect_replay_safety",
                expected="no committed/in-progress/unknown effects",
                observed=state_names,
                status="REJECTED",
            ))
            reason = (
                ResumeReasonCode.UNKNOWN_SIDE_EFFECT
                if any(record.effect_state is SideEffectState.UNKNOWN for record in replay_blocking)
                else ResumeReasonCode.DUPLICATE_SIDE_EFFECT
            )
            return _reject(checkpoint, reason, evidence)
        return _make_decision(
            checkpoint,
            disposition=ResumeDisposition.ALLOW,
            action=ResumeAction.REPLAY_FROM_STAGE,
            reason=ResumeReasonCode.ALLOWED_REPLAY,
            evidence=evidence,
            stage_id=checkpoint.active_stage_id,
            task_id=checkpoint.active_task_id,
        )

    if requested is ResumeAction.REPLAN_FROM_CHECKPOINT:
        if not assessment.replan_allowed:
            return _reject(
                checkpoint,
                assessment.reason_code or ResumeReasonCode.WORKFLOW_INCOMPATIBLE,
                evidence,
            )
        if any(record.effect_state in _UNKNOWN_EFFECTS for record in checkpoint.task_effect_records):
            evidence.append(_evidence(
                "side_effect_replan_safety",
                expected="no unknown/in-progress effects",
                observed=",".join(
                    sorted({record.effect_state.value for record in checkpoint.task_effect_records
                            if record.effect_state in _UNKNOWN_EFFECTS})
                ),
                status="UNKNOWN",
            ))
            return _clarify(
                checkpoint,
                ResumeReasonCode.UNKNOWN_SIDE_EFFECT,
                evidence,
                "旧 Run 的副作用状态尚未确认，无法安全重新规划。",
            )
        return _make_decision(
            checkpoint,
            disposition=ResumeDisposition.ALLOW,
            action=ResumeAction.REPLAN_FROM_CHECKPOINT,
            reason=ResumeReasonCode.ALLOWED_REPLAN,
            evidence=evidence,
            stage_id=checkpoint.active_stage_id or None,
            task_id=checkpoint.active_task_id or None,
        )

    if requested is ResumeAction.RESUME_EXACT:
        if not assessment.exact_allowed:
            return _reject(
                checkpoint,
                assessment.reason_code or ResumeReasonCode.PLAN_INCOMPATIBLE,
                evidence,
            )
        action = ResumeAction.RESUME_EXACT
    else:
        # The old checkpoint proposal is advisory only.  The current facts
        # choose exact or replan again on every validation.
        action = (
            ResumeAction.RESUME_EXACT
            if assessment.exact_allowed
            else ResumeAction.REPLAN_FROM_CHECKPOINT
        )
        if action is ResumeAction.REPLAN_FROM_CHECKPOINT and not assessment.replan_allowed:
            return _reject(
                checkpoint,
                assessment.reason_code or ResumeReasonCode.WORKFLOW_INCOMPATIBLE,
                evidence,
            )

    unsafe_effects = [
        record for record in checkpoint.task_effect_records
        if record.effect_state in _UNKNOWN_EFFECTS
    ]
    if unsafe_effects:
        state_names = ",".join(sorted({record.effect_state.value for record in unsafe_effects}))
        evidence.append(_evidence(
            "side_effect_resume_safety",
            expected="no unknown/in-progress effects",
            observed=state_names,
            status="UNKNOWN",
        ))
        reason = (
            ResumeReasonCode.SIDE_EFFECT_IN_PROGRESS
            if any(record.effect_state in {
                SideEffectState.STARTED,
                SideEffectState.FAILED_AFTER_COMMIT,
            } for record in unsafe_effects)
            else ResumeReasonCode.UNKNOWN_SIDE_EFFECT
        )
        return _clarify(
            checkpoint,
            reason,
            evidence,
            "上一次副作用的最终状态无法确认，请先确认外部结果后再恢复。",
        )

    return _make_decision(
        checkpoint,
        disposition=ResumeDisposition.ALLOW,
        action=action,
        reason=(
            ResumeReasonCode.ALLOWED_EXACT
            if action is ResumeAction.RESUME_EXACT
            else ResumeReasonCode.ALLOWED_REPLAN
        ),
        evidence=evidence,
        stage_id=checkpoint.active_stage_id,
        task_id=checkpoint.active_task_id,
    )


__all__ = ["validate_resume"]
