"""Pure Run-level resume resolver for v2.2C."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.checkpoint import ResumeAction, RuntimeEvidence

from .contracts import (
    RunResumeDisposition,
    RunResumeIndex,
    RunResumeReasonCode,
    RunResumeRequest,
)


@dataclass(frozen=True)
class RunResumeDecision:
    disposition: RunResumeDisposition
    run_id: str
    workflow_action: ResumeAction | None
    selected_workflow_id: str | None
    selected_checkpoint_id: str | None
    skipped_workflow_ids: tuple[str, ...]
    remaining_workflow_ids: tuple[str, ...]
    resulting_status: str
    reason_code: RunResumeReasonCode
    evidence: tuple[RuntimeEvidence, ...] = ()
    clarification_question: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "disposition", RunResumeDisposition(self.disposition))
        if self.workflow_action is not None:
            object.__setattr__(self, "workflow_action", ResumeAction(self.workflow_action))
        object.__setattr__(self, "reason_code", RunResumeReasonCode(self.reason_code))
        if self.disposition is RunResumeDisposition.ALLOW:
            if self.workflow_action is None or self.selected_workflow_id is None:
                raise ValueError("ALLOW 必须选择 Workflow 并提供 workflow_action")
        elif self.workflow_action is not None or self.selected_workflow_id is not None:
            raise ValueError("阻断的 RunResumeDecision 不得选择 Workflow 或 action")
        if (
            self.disposition is RunResumeDisposition.REQUIRE_CLARIFICATION
            and not self.clarification_question
        ):
            raise ValueError("REQUIRE_CLARIFICATION 必须包含 clarification_question")

    def to_dict(self) -> dict[str, Any]:
        return {
            "disposition": self.disposition.value,
            "run_id": self.run_id,
            "workflow_action": self.workflow_action.value if self.workflow_action else None,
            "selected_workflow_id": self.selected_workflow_id,
            "selected_checkpoint_id": self.selected_checkpoint_id,
            "skipped_workflow_ids": list(self.skipped_workflow_ids),
            "remaining_workflow_ids": list(self.remaining_workflow_ids),
            "resulting_status": self.resulting_status,
            "reason_code": self.reason_code.value,
            "evidence": [item.to_dict() for item in self.evidence],
            "clarification_question": self.clarification_question,
        }


def _evidence(
    kind: str,
    expected: str = "",
    observed: str = "",
    status: str = "VERIFIED",
) -> RuntimeEvidence:
    return RuntimeEvidence(
        source="run_resume_resolver",
        kind=kind,
        expected=expected,
        observed=observed,
        status=status,
    )


class RunResumeResolver:
    """Deterministically select one active Workflow or stop safely."""

    @staticmethod
    def resolve(
        index: RunResumeIndex,
        request: RunResumeRequest,
    ) -> RunResumeDecision:
        if request.requested_run_id and request.requested_run_id != index.run_id:
            return RunResumeResolver._reject(
                index,
                RunResumeReasonCode.RUN_MISMATCH,
                _evidence("run_identity", index.run_id, request.requested_run_id, "MISMATCH"),
            )

        if request.candidate_run_ids:
            if index.run_id not in request.candidate_run_ids:
                return RunResumeResolver._reject(
                    index,
                    RunResumeReasonCode.RUN_MISMATCH,
                    _evidence(
                        "candidate_runs",
                        index.run_id,
                        ",".join(request.candidate_run_ids),
                        "MISMATCH",
                    ),
                )
            if len(request.candidate_run_ids) > 1:
                return RunResumeResolver._clarify(
                    index,
                    RunResumeReasonCode.AMBIGUOUS_RUN,
                    _evidence(
                        "candidate_runs",
                        index.run_id,
                        ",".join(request.candidate_run_ids),
                        "UNKNOWN",
                    ),
                    "检测到多个可能的 Run，请明确要恢复哪一个。",
                )

        if not index.active_workflow_id:
            return RunResumeResolver._reject(
                index,
                RunResumeReasonCode.NO_ACTIVE_WORKFLOW,
                _evidence("active_workflow", "one active Workflow", "none", "MISMATCH"),
            )

        active = index.workflow(index.active_workflow_id)
        if active is None or active.checkpoint_id != index.active_checkpoint_id:
            return RunResumeResolver._reject(
                index,
                RunResumeReasonCode.RUN_INDEX_INCONSISTENT,
                _evidence(
                    "active_checkpoint",
                    index.active_checkpoint_id,
                    active.checkpoint_id if active else "missing",
                    "MISMATCH",
                ),
            )

        if request.requested_workflow_id and request.requested_workflow_id != active.workflow_id:
            return RunResumeResolver._clarify(
                index,
                RunResumeReasonCode.RUN_INDEX_INCONSISTENT,
                _evidence(
                    "active_workflow",
                    active.workflow_id,
                    request.requested_workflow_id,
                    "MISMATCH",
                ),
                "当前请求指向的 Workflow 不是 Run 的 active Workflow，请确认目标。",
            )
        if request.requested_checkpoint_id and request.requested_checkpoint_id != active.checkpoint_id:
            return RunResumeResolver._reject(
                index,
                RunResumeReasonCode.ACTIVE_CHECKPOINT_MISMATCH,
                _evidence(
                    "requested_checkpoint",
                    active.checkpoint_id,
                    request.requested_checkpoint_id,
                    "MISMATCH",
                ),
            )

        current_version = request.version_for(active.workflow_id)
        if current_version and current_version != active.workflow_version:
            return RunResumeResolver._reject(
                index,
                RunResumeReasonCode.WORKFLOW_VERSION_INCOMPATIBLE,
                _evidence(
                    "workflow_version",
                    active.workflow_version,
                    current_version,
                    "MISMATCH",
                ),
            )

        workflow_map = {item.workflow_id: item for item in index.workflows}
        for upstream in active.depends_on:
            upstream_summary = workflow_map[upstream]
            if upstream_summary.status.value != "COMPLETED":
                return RunResumeResolver._reject(
                    index,
                    RunResumeReasonCode.UPSTREAM_WORKFLOW_INCOMPLETE,
                    _evidence(
                        "upstream_workflow",
                        "COMPLETED",
                        f"{upstream}:{upstream_summary.status.value}",
                        "MISMATCH",
                    ),
                )

        for requirement in active.required_artifacts:
            observed = request.artifact(requirement.artifact_id)
            if observed is None or not observed.exists or not observed.verified:
                return RunResumeResolver._reject(
                    index,
                    RunResumeReasonCode.UPSTREAM_ARTIFACT_MISSING,
                    _evidence(
                        "upstream_artifact",
                        requirement.expected_digest,
                        observed.digest if observed else "missing",
                        "MISMATCH",
                    ),
                )
            if observed.digest != requirement.expected_digest:
                return RunResumeResolver._reject(
                    index,
                    RunResumeReasonCode.UPSTREAM_ARTIFACT_CHANGED,
                    _evidence(
                        "upstream_artifact",
                        requirement.expected_digest,
                        observed.digest,
                        "MISMATCH",
                    ),
                )

        unsafe_effects = {
            "UNKNOWN", "STARTED", "FAILED_AFTER_COMMIT", "COMMITTED",
        }
        if active.active_side_effect_state in unsafe_effects:
            reason = (
                RunResumeReasonCode.DUPLICATE_SIDE_EFFECT
                if active.active_side_effect_state == "COMMITTED"
                else RunResumeReasonCode.UNKNOWN_SIDE_EFFECT
            )
            return RunResumeResolver._clarify(
                index,
                reason,
                _evidence(
                    "active_side_effect",
                    "safe_to_resume",
                    active.active_side_effect_state,
                    "UNKNOWN",
                ),
                "当前 Workflow 的副作用状态不确定，请先确认外部结果。",
            )

        if (
            request.requested_action is ResumeAction.REPLAY_FROM_STAGE
            and not active.active_stage_idempotent
        ):
            return RunResumeResolver._reject(
                index,
                RunResumeReasonCode.NON_IDEMPOTENT_STAGE,
                _evidence("active_stage_idempotency", "true", "false", "MISMATCH"),
            )

        action = request.requested_action
        reason = (
            RunResumeReasonCode.ALLOWED_REPLAY_ACTIVE_WORKFLOW
            if action is ResumeAction.REPLAY_FROM_STAGE
            else RunResumeReasonCode.ALLOWED_ACTIVE_WORKFLOW
        )
        skipped = tuple(
            workflow_id
            for workflow_id in index.workflow_sequence
            if workflow_id in index.completed_workflow_ids
        )
        return RunResumeDecision(
            disposition=RunResumeDisposition.ALLOW,
            run_id=index.run_id,
            workflow_action=action,
            selected_workflow_id=active.workflow_id,
            selected_checkpoint_id=active.checkpoint_id or None,
            skipped_workflow_ids=skipped,
            remaining_workflow_ids=index.pending_workflow_ids,
            resulting_status="RUNNING",
            reason_code=reason,
            evidence=(_evidence("workflow_boundary", active.workflow_id, active.checkpoint_id),),
        )

    @staticmethod
    def _reject(
        index: RunResumeIndex,
        reason: RunResumeReasonCode,
        evidence: RuntimeEvidence,
    ) -> RunResumeDecision:
        return RunResumeDecision(
            disposition=RunResumeDisposition.REJECT,
            run_id=index.run_id,
            workflow_action=None,
            selected_workflow_id=None,
            selected_checkpoint_id=None,
            skipped_workflow_ids=(),
            remaining_workflow_ids=index.pending_workflow_ids,
            resulting_status="REJECTED",
            reason_code=reason,
            evidence=(evidence,),
        )

    @staticmethod
    def _clarify(
        index: RunResumeIndex,
        reason: RunResumeReasonCode,
        evidence: RuntimeEvidence,
        question: str,
    ) -> RunResumeDecision:
        return RunResumeDecision(
            disposition=RunResumeDisposition.REQUIRE_CLARIFICATION,
            run_id=index.run_id,
            workflow_action=None,
            selected_workflow_id=None,
            selected_checkpoint_id=None,
            skipped_workflow_ids=(),
            remaining_workflow_ids=index.pending_workflow_ids,
            resulting_status="WAITING_USER",
            reason_code=reason,
            evidence=(evidence,),
            clarification_question=question,
        )


__all__ = ["RunResumeDecision", "RunResumeResolver"]
