"""Pure deterministic oracle for the v2.2C seed dataset."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.checkpoint import ResumeAction

from .cases import (
    RunResumeDisposition,
    RunResumeIndex,
    RunResumeReason,
    RunResumeRequest,
)


@dataclass(frozen=True)
class OracleEvidence:
    kind: str
    expected: str = ""
    observed: str = ""
    status: str = "VERIFIED"

    def to_dict(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "expected": self.expected,
            "observed": self.observed,
            "status": self.status,
        }


@dataclass(frozen=True)
class RunResumeDecision:
    disposition: str
    run_id: str
    workflow_action: str | None
    selected_workflow_id: str | None
    selected_checkpoint_id: str | None
    skipped_workflow_ids: tuple[str, ...]
    remaining_workflow_ids: tuple[str, ...]
    resulting_status: str
    reason_code: str
    evidence: tuple[OracleEvidence, ...] = ()
    clarification_question: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "disposition": self.disposition,
            "run_id": self.run_id,
            "workflow_action": self.workflow_action,
            "selected_workflow_id": self.selected_workflow_id,
            "selected_checkpoint_id": self.selected_checkpoint_id,
            "skipped_workflow_ids": list(self.skipped_workflow_ids),
            "remaining_workflow_ids": list(self.remaining_workflow_ids),
            "resulting_status": self.resulting_status,
            "reason_code": self.reason_code,
            "evidence": [item.to_dict() for item in self.evidence],
            "clarification_question": self.clarification_question,
        }


def _decision(
    index: RunResumeIndex,
    *,
    disposition: str,
    reason: str,
    action: str | None = None,
    selected: str | None = None,
    checkpoint: str | None = None,
    skipped: tuple[str, ...] = (),
    remaining: tuple[str, ...] = (),
    status: str,
    evidence: tuple[OracleEvidence, ...] = (),
    question: str | None = None,
) -> RunResumeDecision:
    return RunResumeDecision(
        disposition=disposition,
        run_id=index.run_id,
        workflow_action=action,
        selected_workflow_id=selected,
        selected_checkpoint_id=checkpoint,
        skipped_workflow_ids=skipped,
        remaining_workflow_ids=remaining,
        resulting_status=status,
        reason_code=reason,
        evidence=evidence,
        clarification_question=question,
    )


def evaluate(index: RunResumeIndex, request: RunResumeRequest) -> RunResumeDecision:
    """Select one active Workflow or produce a visible safe stop.

    This is a benchmark oracle, not the v2.2C production Orchestrator.  It
    only coordinates Run-level facts and leaves Stage/Task validation to the
    v2.2B ``ResumeValidator``.
    """
    evidence: list[OracleEvidence] = []
    if request.requested_run_id and request.requested_run_id != index.run_id:
        return _decision(
            index,
            disposition=RunResumeDisposition.REJECT,
            reason=RunResumeReason.RUN_MISMATCH,
            status="REJECTED",
            evidence=(OracleEvidence(
                "run_identity", index.run_id, request.requested_run_id, "MISMATCH"
            ),),
        )

    if request.candidate_run_ids:
        matching = tuple(item for item in request.candidate_run_ids if item == index.run_id)
        if not matching:
            return _decision(
                index,
                disposition=RunResumeDisposition.REJECT,
                reason=RunResumeReason.RUN_MISMATCH,
                status="REJECTED",
                evidence=(OracleEvidence(
                    "candidate_runs", index.run_id, ",".join(request.candidate_run_ids), "MISMATCH"
                ),),
            )
        if len(request.candidate_run_ids) > 1:
            return _decision(
                index,
                disposition=RunResumeDisposition.REQUIRE_CLARIFICATION,
                reason=RunResumeReason.AMBIGUOUS_RUN,
                status="WAITING_USER",
                evidence=(OracleEvidence(
                    "candidate_runs", index.run_id, ",".join(request.candidate_run_ids), "UNKNOWN"
                ),),
                question="检测到多个可能的 Run，请明确要恢复哪一个。",
            )

    active = index.workflow(index.active_workflow_id)
    if active is None or active.checkpoint_id != index.active_checkpoint_id:
        return _decision(
            index,
            disposition=RunResumeDisposition.REJECT,
            reason=RunResumeReason.RUN_INDEX_INCONSISTENT,
            status="REJECTED",
            evidence=(OracleEvidence(
                "active_checkpoint", index.active_checkpoint_id,
                active.checkpoint_id if active else "missing", "MISMATCH"
            ),),
        )
    if request.requested_checkpoint_id and request.requested_checkpoint_id != active.checkpoint_id:
        return _decision(
            index,
            disposition=RunResumeDisposition.REJECT,
            reason=RunResumeReason.ACTIVE_CHECKPOINT_MISMATCH,
            status="REJECTED",
            evidence=(OracleEvidence(
                "requested_checkpoint", active.checkpoint_id,
                request.requested_checkpoint_id, "MISMATCH"
            ),),
        )

    if request.requested_workflow_id and request.requested_workflow_id != active.workflow_id:
        return _decision(
            index,
            disposition=RunResumeDisposition.REQUIRE_CLARIFICATION,
            reason=RunResumeReason.RUN_INDEX_INCONSISTENT,
            status="WAITING_USER",
            evidence=(OracleEvidence(
                "active_workflow", active.workflow_id,
                request.requested_workflow_id, "MISMATCH"
            ),),
            question="当前请求指向的 Workflow 不是 Run 的 active Workflow，请确认目标。",
        )

    current_version = request.version_for(active.workflow_id)
    if current_version and current_version != active.version:
        return _decision(
            index,
            disposition=RunResumeDisposition.REJECT,
            reason=RunResumeReason.WORKFLOW_VERSION_INCOMPATIBLE,
            status="REJECTED",
            evidence=(OracleEvidence(
                "workflow_version", active.version, current_version, "MISMATCH"
            ),),
        )

    workflow_map = {item.workflow_id: item for item in index.workflows}
    for upstream in active.depends_on:
        if workflow_map[upstream].status != "COMPLETED":
            return _decision(
                index,
                disposition=RunResumeDisposition.REJECT,
                reason=RunResumeReason.UPSTREAM_WORKFLOW_INCOMPLETE,
                status="REJECTED",
                remaining=index.pending_workflow_ids,
                evidence=(OracleEvidence(
                    "upstream_workflow", "COMPLETED", f"{upstream}:{workflow_map[upstream].status}", "MISMATCH"
                ),),
            )

    for requirement in active.required_artifacts:
        observed = request.artifact(requirement.artifact_id)
        if observed is None or not observed.exists or not observed.verified:
            return _decision(
                index,
                disposition=RunResumeDisposition.REJECT,
                reason=RunResumeReason.UPSTREAM_ARTIFACT_MISSING,
                status="REJECTED",
                evidence=(OracleEvidence(
                    "upstream_artifact", requirement.expected_digest,
                    observed.digest if observed else "missing", "MISMATCH"
                ),),
            )
        if observed.digest != requirement.expected_digest:
            return _decision(
                index,
                disposition=RunResumeDisposition.REJECT,
                reason=RunResumeReason.UPSTREAM_ARTIFACT_CHANGED,
                status="REJECTED",
                evidence=(OracleEvidence(
                    "upstream_artifact", requirement.expected_digest,
                    observed.digest, "MISMATCH"
                ),),
            )

    if active.active_side_effect_state in {
        "UNKNOWN", "STARTED", "FAILED_AFTER_COMMIT", "COMMITTED",
    }:
        reason = (
            RunResumeReason.DUPLICATE_SIDE_EFFECT
            if active.active_side_effect_state == "COMMITTED"
            else RunResumeReason.UNKNOWN_SIDE_EFFECT
        )
        return _decision(
            index,
            disposition=RunResumeDisposition.REQUIRE_CLARIFICATION,
            reason=reason,
            status="WAITING_USER",
            evidence=(OracleEvidence(
                "active_side_effect", "safe_to_resume", active.active_side_effect_state, "UNKNOWN"
            ),),
            question="当前 Workflow 的副作用状态不确定，请先确认外部结果。",
        )

    action = request.requested_action or ResumeAction.RESUME_EXACT.value
    if action == ResumeAction.REPLAY_FROM_STAGE.value and not active.active_stage_idempotent:
        return _decision(
            index,
            disposition=RunResumeDisposition.REJECT,
            reason=RunResumeReason.NON_IDEMPOTENT_STAGE,
            status="REJECTED",
            evidence=(OracleEvidence(
                "active_stage_idempotency", "true", "false", "MISMATCH"
            ),),
        )
    if action not in {
        ResumeAction.RESUME_EXACT.value,
        ResumeAction.REPLAY_FROM_STAGE.value,
    }:
        return _decision(
            index,
            disposition=RunResumeDisposition.REJECT,
            reason=RunResumeReason.RUN_INDEX_INCONSISTENT,
            status="REJECTED",
            evidence=(OracleEvidence(
                "workflow_action", "RESUME_EXACT|REPLAY_FROM_STAGE", action, "MISMATCH"
            ),),
        )

    if action == ResumeAction.REPLAY_FROM_STAGE.value:
        reason = RunResumeReason.ALLOWED_REPLAY_ACTIVE_WORKFLOW
    else:
        reason = RunResumeReason.ALLOWED_ACTIVE_WORKFLOW
    skipped = tuple(item for item in index.workflow_sequence if item in index.completed_workflow_ids)
    remaining = tuple(item for item in index.pending_workflow_ids)
    evidence.append(OracleEvidence(
        "workflow_boundary", active.workflow_id, active.checkpoint_id, "VERIFIED"
    ))
    return _decision(
        index,
        disposition=RunResumeDisposition.ALLOW,
        reason=reason,
        action=action,
        selected=active.workflow_id,
        checkpoint=active.checkpoint_id,
        skipped=skipped,
        remaining=remaining,
        status="RUNNING",
        evidence=tuple(evidence),
    )


__all__ = ["OracleEvidence", "RunResumeDecision", "evaluate"]
