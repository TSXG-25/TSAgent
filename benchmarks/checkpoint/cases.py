"""Pure v2.2A Checkpoint fixtures.

These fixtures validate schema/validator behavior only.  They do not claim
that a real Workflow Runtime can create or execute a checkpoint; that is the
v2.2B/C scope.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.checkpoint import (
    CheckpointStatus,
    CompatibilityRegistry,
    ExternalStateGuard,
    GuardStatus,
    ResumeAction,
    ResumeContext,
    ResumeDisposition,
    ResumeReasonCode,
    RunCheckpoint,
    SideEffectState,
    TaskEffectRecord,
    WorkflowMigration,
)


CHECKPOINT_BENCHMARK_NAME = "run-checkpoint-v2.2a"
CHECKPOINT_BENCHMARK_VERSION = "v0.1"
CHECKPOINT_VALIDATOR_VERSION = "adr-0016-v1"


@dataclass(frozen=True)
class CheckpointCase:
    id: str
    group: str
    checkpoint: dict[str, Any]
    context: dict[str, Any]
    expected_disposition: str
    expected_action: str | None
    expected_reason: str
    expected_resulting_status: str
    registry: dict[str, Any]
    oracle_only: bool = False
    note: str = ""

    def to_dict(self, *, include_note: bool = True) -> dict[str, Any]:
        value = {
            "id": self.id,
            "group": self.group,
            "checkpoint": self.checkpoint,
            "context": self.context,
            "expected_disposition": self.expected_disposition,
            "expected_action": self.expected_action,
            "expected_reason": self.expected_reason,
            "expected_resulting_status": self.expected_resulting_status,
            "registry": self.registry,
            "oracle_only": self.oracle_only,
        }
        if include_note:
            value["note"] = self.note
        return value


def _registry(
    *,
    schema: str = "1.0",
    contract: str = "v2.2A",
    migrations: tuple[WorkflowMigration, ...] = (),
) -> dict[str, Any]:
    return {
        "current_checkpoint_schema_version": schema,
        "current_contract_version": contract,
        "workflow_migrations": [
            {
                "workflow_id": item.workflow_id,
                "from_version": item.from_version,
                "to_version": item.to_version,
                "migration_id": item.migration_id,
            }
            for item in migrations
        ],
    }


def _context(
    *,
    run_id: str = "run-001",
    workflow_id: str = "workflow.demo",
    workflow_version: str = "1.0.0",
    plan_version: str = "1.0.0",
    action: ResumeAction | None = None,
    target: str = "output/result.txt",
    candidate_run_ids: tuple[str, ...] = (),
    stage_idempotent: bool = False,
    requested_stage_id: str = "",
    required_permissions: tuple[str, ...] = (),
    available_permissions: tuple[str, ...] = (),
    guards: tuple[ExternalStateGuard, ...] = (),
) -> dict[str, Any]:
    return ResumeContext(
        workflow_id=workflow_id,
        workflow_version=workflow_version,
        plan_version=plan_version,
        requested_action=action,
        requested_target=target,
        candidate_run_ids=candidate_run_ids,
        required_permissions=required_permissions,
        available_permissions=available_permissions,
        requested_stage_id=requested_stage_id,
        stage_idempotent=stage_idempotent,
        external_state_evidence=guards,
    ).to_dict()


def _checkpoint(
    *,
    run_id: str = "run-001",
    checkpoint_id: str = "cp-001",
    status: CheckpointStatus = CheckpointStatus.SUSPENDED,
    workflow_id: str = "workflow.demo",
    workflow_version: str = "1.0.0",
    plan_version: str = "1.0.0",
    target: str = "output/result.txt",
    active_stage_id: str = "stage.write",
    active_task_id: str = "task.write",
    completed_task_ids: tuple[str, ...] = (),
    effects: tuple[TaskEffectRecord, ...] = (),
    invalidation_reasons: tuple[str, ...] = (),
    schema: str = "1.0",
    contract: str = "v2.2A",
    proposed_action: ResumeAction | None = None,
) -> dict[str, Any]:
    return RunCheckpoint(
        run_id=run_id,
        checkpoint_id=checkpoint_id,
        parent_checkpoint_id=None,
        sequence_number=0,
        session_id="session-001",
        conversation_id="conversation-001",
        user_scope="user-001",
        workflow_id=workflow_id,
        workflow_version=workflow_version,
        plan_version=plan_version,
        active_stage_id=active_stage_id,
        active_task_id=active_task_id,
        status=status,
        execution_plan={
            "task_id": active_task_id,
            "workflow_id": workflow_id,
            "steps": [{"id": "step-1", "tool": "filesystem.write"}],
        },
        target_summary=target,
        completed_task_ids=completed_task_ids,
        verifier_status="VERIFIED",
        task_effect_records=effects,
        idempotency_keys=tuple(
            item.idempotency_key for item in effects if item.idempotency_key
        ),
        invalidation_reasons=invalidation_reasons,
        checkpoint_schema_version=schema,
        contract_version=contract,
        proposed_next_action=proposed_action,
        created_at="2026-08-05T00:00:00Z",
        updated_at="2026-08-05T00:01:00Z",
    ).to_dict()


def _case(
    case_id: str,
    group: str,
    *,
    checkpoint: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
    expected_disposition: ResumeDisposition,
    expected_action: ResumeAction | None,
    expected_reason: ResumeReasonCode,
    expected_resulting_status: CheckpointStatus,
    registry: dict[str, Any] | None = None,
    oracle_only: bool = False,
    note: str = "",
) -> CheckpointCase:
    return CheckpointCase(
        id=case_id,
        group=group,
        checkpoint=checkpoint or _checkpoint(),
        context=context or _context(),
        expected_disposition=expected_disposition.value,
        expected_action=expected_action.value if expected_action else None,
        expected_reason=expected_reason.value,
        expected_resulting_status=expected_resulting_status.value,
        registry=registry or _registry(),
        oracle_only=oracle_only,
        note=note,
    )


def build_cases() -> list[CheckpointCase]:
    """Build the v2.2A dataset with pure-validator and oracle fixtures."""
    cases: list[CheckpointCase] = []
    cases.append(_case(
        "exact-001", "exact_resume_validation",
        expected_disposition=ResumeDisposition.ALLOW,
        expected_action=ResumeAction.RESUME_EXACT,
        expected_reason=ResumeReasonCode.ALLOWED_EXACT,
        expected_resulting_status=CheckpointStatus.RUNNING,
        note="相同 Workflow/Plan 版本，副作用为空，允许精确恢复",
    ))
    cases.append(_case(
        "exact-002", "exact_resume_validation",
        checkpoint=_checkpoint(
            completed_task_ids=("task.previous",),
            effects=(TaskEffectRecord(
                task_id="task.previous",
                tool_name="filesystem.write",
                operation_type="write",
                idempotency_key="effect-previous",
                effect_state=SideEffectState.COMMITTED,
            ),),
        ),
        expected_disposition=ResumeDisposition.ALLOW,
        expected_action=ResumeAction.RESUME_EXACT,
        expected_reason=ResumeReasonCode.ALLOWED_EXACT,
        expected_resulting_status=CheckpointStatus.RUNNING,
        note="已提交的前置任务不得重复执行，但可从后续边界继续",
    ))

    cases.append(_case(
        "stale-001", "stale_checkpoint_rejection",
        checkpoint=_checkpoint(invalidation_reasons=("workspace_changed",)),
        expected_disposition=ResumeDisposition.REJECT,
        expected_action=None,
        expected_reason=ResumeReasonCode.CHECKPOINT_INVALIDATED,
        expected_resulting_status=CheckpointStatus.SUSPENDED,
        note="Checkpoint 已被外部事实标记失效",
    ))
    cases.append(_case(
        "stale-002", "stale_checkpoint_rejection",
        context=_context(guards=(ExternalStateGuard(
            resource_id="output/result.txt",
            guard_type="FILE_CONTENT_HASH",
            expected_value="sha256:old",
            observed_value="sha256:new",
            status=GuardStatus.MISMATCH,
        ),)),
        expected_disposition=ResumeDisposition.REJECT,
        expected_action=None,
        expected_reason=ResumeReasonCode.EXTERNAL_STATE_MISMATCH,
        expected_resulting_status=CheckpointStatus.SUSPENDED,
        note="外部资源版本不一致，禁止盲目恢复",
    ))

    cases.append(_case(
        "version-001", "version_incompatibility",
        checkpoint=_checkpoint(plan_version="1.0.0"),
        context=_context(plan_version="2.0.0"),
        expected_disposition=ResumeDisposition.ALLOW,
        expected_action=ResumeAction.REPLAN_FROM_CHECKPOINT,
        expected_reason=ResumeReasonCode.ALLOWED_REPLAN,
        expected_resulting_status=CheckpointStatus.RUNNING,
        note="Plan 版本变化只允许 Replan",
    ))
    cases.append(_case(
        "version-002", "version_incompatibility",
        checkpoint=_checkpoint(workflow_version="1.0.0"),
        context=_context(workflow_version="2.0.0"),
        expected_disposition=ResumeDisposition.REJECT,
        expected_action=None,
        expected_reason=ResumeReasonCode.WORKFLOW_INCOMPATIBLE,
        expected_resulting_status=CheckpointStatus.SUSPENDED,
        note="Workflow 版本变化且无 migration mapping",
    ))
    cases.append(_case(
        "version-003", "version_incompatibility",
        checkpoint=_checkpoint(schema="2.0"),
        expected_disposition=ResumeDisposition.REJECT,
        expected_action=None,
        expected_reason=ResumeReasonCode.SCHEMA_INCOMPATIBLE,
        expected_resulting_status=CheckpointStatus.SUSPENDED,
        note="Checkpoint schema major 不兼容",
    ))
    cases.append(_case(
        "version-004", "version_incompatibility",
        checkpoint=_checkpoint(workflow_version="1.0.0"),
        context=_context(workflow_version="2.0.0"),
        registry=_registry(migrations=(WorkflowMigration(
            workflow_id="workflow.demo",
            from_version="1.0.0",
            to_version="2.0.0",
            migration_id="workflow.demo.1-to-2",
        ),)),
        expected_disposition=ResumeDisposition.ALLOW,
        expected_action=ResumeAction.REPLAN_FROM_CHECKPOINT,
        expected_reason=ResumeReasonCode.ALLOWED_REPLAN,
        expected_resulting_status=CheckpointStatus.RUNNING,
        note="显式 migration mapping 允许 Replan，不允许 Exact",
    ))

    cases.append(_case(
        "effect-001", "duplicate_side_effect_blocking",
        checkpoint=_checkpoint(effects=(TaskEffectRecord(
            task_id="task.write",
            tool_name="filesystem.write",
            operation_type="write",
            idempotency_key="effect-committed",
            effect_state=SideEffectState.COMMITTED,
        ),)),
        context=_context(
            action=ResumeAction.REPLAY_FROM_STAGE,
            stage_idempotent=True,
        ),
        expected_disposition=ResumeDisposition.REJECT,
        expected_action=None,
        expected_reason=ResumeReasonCode.DUPLICATE_SIDE_EFFECT,
        expected_resulting_status=CheckpointStatus.SUSPENDED,
        note="Committed side effect 禁止 Replay",
    ))
    cases.append(_case(
        "effect-002", "duplicate_side_effect_blocking",
        context=_context(
            action=ResumeAction.REPLAY_FROM_STAGE,
            stage_idempotent=False,
        ),
        expected_disposition=ResumeDisposition.REJECT,
        expected_action=None,
        expected_reason=ResumeReasonCode.NON_IDEMPOTENT_STAGE,
        expected_resulting_status=CheckpointStatus.SUSPENDED,
        note="没有 Stage 幂等声明，禁止 Replay",
    ))
    cases.append(_case(
        "effect-003", "duplicate_side_effect_blocking",
        checkpoint=_checkpoint(effects=(TaskEffectRecord(
            task_id="task.write",
            effect_state=SideEffectState.UNKNOWN,
        ),)),
        expected_disposition=ResumeDisposition.REQUIRE_CLARIFICATION,
        expected_action=None,
        expected_reason=ResumeReasonCode.UNKNOWN_SIDE_EFFECT,
        expected_resulting_status=CheckpointStatus.WAITING_USER,
        note="未知副作用状态不能被映射成任何 ResumeAction",
    ))

    cases.append(_case(
        "determinism-001", "resume_action_determinism",
        checkpoint=_checkpoint(proposed_action=ResumeAction.REPLAY_FROM_STAGE),
        expected_disposition=ResumeDisposition.ALLOW,
        expected_action=ResumeAction.RESUME_EXACT,
        expected_reason=ResumeReasonCode.ALLOWED_EXACT,
        expected_resulting_status=CheckpointStatus.RUNNING,
        note="旧 proposed_next_action 不能覆盖当前 Validator 决策",
    ))
    cases.append(_case(
        "determinism-002", "resume_action_determinism",
        context=_context(action=ResumeAction.REPLAN_FROM_CHECKPOINT),
        expected_disposition=ResumeDisposition.ALLOW,
        expected_action=ResumeAction.REPLAN_FROM_CHECKPOINT,
        expected_reason=ResumeReasonCode.ALLOWED_REPLAN,
        expected_resulting_status=CheckpointStatus.RUNNING,
        note="显式 Replan 动作的决策可重复",
    ))
    cases.append(_case(
        "determinism-003", "resume_action_determinism",
        context=_context(action=ResumeAction.ABANDON_AND_RESTART),
        expected_disposition=ResumeDisposition.ALLOW,
        expected_action=ResumeAction.ABANDON_AND_RESTART,
        expected_reason=ResumeReasonCode.ALLOWED_ABANDON,
        expected_resulting_status=CheckpointStatus.CANCELLED,
        note="显式 Abandon 与安全拒绝/澄清分离",
    ))

    cases.append(_case(
        "fixture-conflict-001", "conflict_resume",
        context=_context(target="output/other.txt"),
        expected_disposition=ResumeDisposition.REQUIRE_CLARIFICATION,
        expected_action=None,
        expected_reason=ResumeReasonCode.TARGET_CONFLICT,
        expected_resulting_status=CheckpointStatus.WAITING_USER,
        oracle_only=True,
        note="v2.2A fixture/oracle：输入目标与 checkpoint 冲突",
    ))
    cases.append(_case(
        "fixture-cross-workflow-001", "cross_workflow_resume",
        context=_context(candidate_run_ids=("run-001", "run-002")),
        expected_disposition=ResumeDisposition.REQUIRE_CLARIFICATION,
        expected_action=None,
        expected_reason=ResumeReasonCode.AMBIGUOUS_RUN,
        expected_resulting_status=CheckpointStatus.WAITING_USER,
        oracle_only=True,
        note="v2.2A fixture/oracle：多 Run 未完成寻址",
    ))
    return cases


def build_registry(payload: dict[str, Any]) -> CompatibilityRegistry:
    return CompatibilityRegistry(
        current_checkpoint_schema_version=str(
            payload.get("current_checkpoint_schema_version", "1.0")
        ),
        current_contract_version=str(payload.get("current_contract_version", "v2.2A")),
        workflow_migrations=tuple(
            WorkflowMigration(
                workflow_id=str(item["workflow_id"]),
                from_version=str(item["from_version"]),
                to_version=str(item["to_version"]),
                migration_id=str(item["migration_id"]),
            )
            for item in payload.get("workflow_migrations", [])
        ),
    )


def summarize(cases: list[CheckpointCase]) -> dict[tuple[str, bool], int]:
    from collections import Counter

    return Counter((case.group, case.oracle_only) for case in cases)


__all__ = [
    "CHECKPOINT_BENCHMARK_NAME",
    "CHECKPOINT_BENCHMARK_VERSION",
    "CHECKPOINT_VALIDATOR_VERSION",
    "CheckpointCase",
    "build_cases",
    "build_registry",
    "summarize",
]
