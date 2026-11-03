"""Pure deterministic ResumeValidator for v2.2A.

The validator consumes a checkpoint plus already-collected current facts.  It
never reads a file, calls a tool, queries a database, or asks an LLM.
"""
from __future__ import annotations

from datetime import datetime
from typing import Iterable

from .compatibility import CompatibilityRegistry, assess_compatibility
from .contracts import (
    ExternalStateGuard,
    NoEffectConfirmation,
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
    no_effect_confirmation: NoEffectConfirmation | None = None,
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
    if checkpoint.external_state_guards:
        guards_by_identity: dict[tuple[str, str], ExternalStateGuard] = {}
        for guard in guards:
            identity = (guard.resource_id, guard.guard_type)
            if identity in guards_by_identity:
                evidence.append(_evidence(
                    "external_state_guard",
                    expected="one observation per required resource and guard type",
                    observed="duplicate observation",
                    status="MISMATCH",
                    detail=f"{guard.guard_type}:{guard.resource_id}",
                ))
                return _reject(
                    checkpoint,
                    ResumeReasonCode.EXTERNAL_STATE_MISMATCH,
                    evidence,
                )
            guards_by_identity[identity] = guard

        required_identities = {
            (guard.resource_id, guard.guard_type)
            for guard in checkpoint.external_state_guards
        }
        unexpected = set(guards_by_identity) - required_identities
        if unexpected:
            resource_id, guard_type = sorted(unexpected)[0]
            evidence.append(_evidence(
                "external_state_guard",
                expected="checkpoint-bound resource and guard type",
                observed=f"{guard_type}:{resource_id}",
                status="MISMATCH",
            ))
            return _reject(
                checkpoint,
                ResumeReasonCode.EXTERNAL_STATE_MISMATCH,
                evidence,
            )

        for expected_guard in checkpoint.external_state_guards:
            identity = (expected_guard.resource_id, expected_guard.guard_type)
            observed_guard = guards_by_identity.get(identity)
            if observed_guard is None:
                evidence.append(_evidence(
                    "external_state_guard",
                    expected=expected_guard.expected_value,
                    observed="missing matching resource observation",
                    status="MISSING",
                    detail=f"{expected_guard.guard_type}:{expected_guard.resource_id}",
                ))
                return _clarify(
                    checkpoint,
                    ResumeReasonCode.EXTERNAL_STATE_UNKNOWN,
                    evidence,
                    "恢复所需的外部状态尚未针对原资源重新确认。",
                )
            if observed_guard.status is GuardStatus.MISMATCH:
                evidence.append(_evidence(
                    "external_state_guard",
                    expected=expected_guard.expected_value,
                    observed=observed_guard.observed_value,
                    status="MISMATCH",
                    detail=f"{expected_guard.guard_type}:{expected_guard.resource_id}",
                ))
                return _reject(
                    checkpoint,
                    ResumeReasonCode.EXTERNAL_STATE_MISMATCH,
                    evidence,
                )
            if observed_guard.status in {GuardStatus.UNKNOWN, GuardStatus.MISSING}:
                evidence.append(_evidence(
                    "external_state_guard",
                    expected=expected_guard.expected_value,
                    observed=observed_guard.observed_value,
                    status=observed_guard.status.value,
                    detail=f"{expected_guard.guard_type}:{expected_guard.resource_id}",
                ))
                return _clarify(
                    checkpoint,
                    ResumeReasonCode.EXTERNAL_STATE_UNKNOWN,
                    evidence,
                    "恢复所需的外部状态无法确认，请先确认资源状态后再继续。",
                )
            if not expected_guard.expected_value:
                evidence.append(_evidence(
                    "external_state_guard",
                    expected="checkpoint-bound expected value",
                    observed="missing expected value",
                    status="MISSING",
                    detail=f"{expected_guard.guard_type}:{expected_guard.resource_id}",
                ))
                return _clarify(
                    checkpoint,
                    ResumeReasonCode.EXTERNAL_STATE_UNKNOWN,
                    evidence,
                    "Checkpoint 没有足够的预期状态信息，请先补充明确的外部状态确认。",
                )
            if observed_guard.expected_value != expected_guard.expected_value:
                evidence.append(_evidence(
                    "external_state_guard",
                    expected=expected_guard.expected_value,
                    observed=observed_guard.expected_value,
                    status="MISMATCH",
                    detail=f"{expected_guard.guard_type}:{expected_guard.resource_id}",
                ))
                return _reject(
                    checkpoint,
                    ResumeReasonCode.EXTERNAL_STATE_MISMATCH,
                    evidence,
                )

            try:
                checked_at = datetime.fromisoformat(
                    observed_guard.checked_at.replace("Z", "+00:00")
                )
                previous_check = (
                    datetime.fromisoformat(
                        expected_guard.checked_at.replace("Z", "+00:00")
                    )
                    if expected_guard.checked_at
                    else None
                )
                current_is_fresh = checked_at.tzinfo is not None and (
                    previous_check is None
                    or (
                        previous_check.tzinfo is not None
                        and checked_at > previous_check
                    )
                )
            except ValueError:
                current_is_fresh = False
            if not current_is_fresh:
                evidence.append(_evidence(
                    "external_state_guard",
                    expected="fresh timezone-aware observation after checkpoint",
                    observed=observed_guard.checked_at or "missing timestamp",
                    status="UNKNOWN",
                    detail=f"{expected_guard.guard_type}:{expected_guard.resource_id}",
                ))
                return _clarify(
                    checkpoint,
                    ResumeReasonCode.EXTERNAL_STATE_UNKNOWN,
                    evidence,
                    "恢复所需的外部状态证据缺失有效的新鲜检查时间。",
                )
            if observed_guard.observed_value != expected_guard.expected_value:
                evidence.append(_evidence(
                    "external_state_guard",
                    expected=expected_guard.expected_value,
                    observed=observed_guard.observed_value,
                    status="MISMATCH",
                    detail=f"{expected_guard.guard_type}:{expected_guard.resource_id}",
                ))
                return _reject(
                    checkpoint,
                    ResumeReasonCode.EXTERNAL_STATE_MISMATCH,
                    evidence,
                )
            evidence.append(_evidence(
                "external_state_guard",
                expected=expected_guard.expected_value,
                observed=observed_guard.observed_value,
                status="VERIFIED",
                detail=(
                    f"{expected_guard.guard_type}:{expected_guard.resource_id};"
                    f"checked_at={observed_guard.checked_at}"
                ),
            ))
    else:
        for guard in guards:
            if guard.status is GuardStatus.MISMATCH:
                evidence.append(_evidence(
                    "external_state_guard",
                    expected=guard.expected_value,
                    observed=guard.observed_value,
                    status="MISMATCH",
                    detail=f"{guard.guard_type}:{guard.resource_id}",
                ))
                return _reject(
                    checkpoint,
                    ResumeReasonCode.EXTERNAL_STATE_MISMATCH,
                    evidence,
                )
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
    if (
        no_effect_confirmation is not None
        and requested is not ResumeAction.RESUME_EXACT
    ):
        evidence.append(_evidence(
            "no_effect_confirmation",
            expected=ResumeAction.RESUME_EXACT.value,
            observed=requested.value if requested else "unspecified action",
            status="MISMATCH",
        ))
        return _reject(checkpoint, ResumeReasonCode.UNKNOWN_SIDE_EFFECT, evidence)

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
    if no_effect_confirmation is not None:
        if not unsafe_effects:
            evidence.append(_evidence(
                "no_effect_confirmation",
                expected="one matching uncertain active task effect",
                observed="no uncertain effect",
                status="MISMATCH",
            ))
            return _reject(checkpoint, ResumeReasonCode.UNKNOWN_SIDE_EFFECT, evidence)
        if len(unsafe_effects) != 1 or unsafe_effects[0].task_id != checkpoint.active_task_id:
            evidence.append(_evidence(
                "no_effect_confirmation",
                expected=f"one uncertain effect for active task {checkpoint.active_task_id}",
                observed=",".join(record.task_id for record in unsafe_effects),
                status="MISMATCH",
            ))
            return _clarify(
                checkpoint,
                ResumeReasonCode.UNKNOWN_SIDE_EFFECT,
                evidence,
                "存在多个或非当前任务的未决副作用；本次确认无法覆盖它们，请先分别核对并处理。",
            )

        record = unsafe_effects[0]
        expected_binding = (
            record.task_id,
            record.idempotency_key,
            record.external_reference,
        )
        observed_binding = (
            no_effect_confirmation.task_id,
            no_effect_confirmation.idempotency_key,
            no_effect_confirmation.external_reference,
        )
        if not record.idempotency_key or not record.external_reference:
            evidence.append(_evidence(
                "no_effect_confirmation",
                expected="checkpoint effect with idempotency key and external reference",
                observed="effect binding is incomplete",
                status="MISSING",
                detail=record.task_id,
            ))
            return _clarify(
                checkpoint,
                ResumeReasonCode.UNKNOWN_SIDE_EFFECT,
                evidence,
                "Checkpoint 缺少可绑定确认的幂等键或外部引用，不能安全续跑；请先通过原系统核实并补全副作用记录。",
            )
        if observed_binding != expected_binding:
            evidence.append(_evidence(
                "no_effect_confirmation",
                expected="|".join(expected_binding),
                observed="|".join(observed_binding),
                status="MISMATCH",
            ))
            return _reject(checkpoint, ResumeReasonCode.UNKNOWN_SIDE_EFFECT, evidence)

        try:
            checked_at = datetime.fromisoformat(
                no_effect_confirmation.checked_at.replace("Z", "+00:00")
            )
            checkpoint_updated_at = datetime.fromisoformat(
                checkpoint.updated_at.replace("Z", "+00:00")
            )
            timestamp_is_fresh = (
                checked_at.tzinfo is not None
                and checked_at.utcoffset() is not None
                and checkpoint_updated_at.tzinfo is not None
                and checkpoint_updated_at.utcoffset() is not None
                and checked_at > checkpoint_updated_at
            )
        except ValueError:
            timestamp_is_fresh = False
        if not timestamp_is_fresh:
            evidence.append(_evidence(
                "no_effect_confirmation",
                expected="timezone-aware checked_at after checkpoint.updated_at",
                observed=no_effect_confirmation.checked_at,
                status="STALE_OR_INVALID",
                detail=checkpoint.updated_at,
            ))
            return _clarify(
                checkpoint,
                ResumeReasonCode.UNKNOWN_SIDE_EFFECT,
                evidence,
                "请先重新核验该副作用，并提交晚于当前 Checkpoint 的带时区 checked_at。",
            )

        evidence.append(_evidence(
            "no_effect_confirmation",
            expected="effect not committed",
            observed="caller confirmed no effect",
            status="ATTESTED",
            detail=(
                f"task_id={record.task_id};"
                f"idempotency_key={record.idempotency_key};"
                f"external_reference={record.external_reference};"
                f"checked_at={no_effect_confirmation.checked_at}"
            ),
        ))
        unsafe_effects = []

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
            (
                "上一次副作用状态无法确认。请先在对应外部系统核实；若确认未发生，"
                "可提交 no_effect_confirmation，绑定 task_id="
                f"{unsafe_effects[0].task_id}、"
                f"idempotency_key={unsafe_effects[0].idempotency_key}、"
                f"external_reference={unsafe_effects[0].external_reference}，"
                "并提供晚于当前 Checkpoint 的带时区 checked_at。"
                "若效果已发生或仍不确定，请勿确认。"
                if len(unsafe_effects) == 1
                and unsafe_effects[0].idempotency_key
                and unsafe_effects[0].external_reference
                else "当前副作用记录缺少幂等键或外部引用，无法绑定安全确认；请先在原系统核实并补全记录。"
                if len(unsafe_effects) == 1
                else "存在多个未决副作用，请先分别核实外部结果；未能确定每个副作用的状态前不要续跑。"
            ),
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
