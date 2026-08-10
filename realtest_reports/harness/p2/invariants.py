"""Deterministic Runtime invariant evaluation for all P2 harness groups."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .evidence import RunTraceEvidence


_TERMINAL_EVENTS = {
    "COMPLETED": "run_completed",
    "FAILED": "run_failed",
    "FAILED_TERMINAL": "run_failed",
    "BLOCKED": "run_blocked",
}


@dataclass(frozen=True)
class RuntimeInvariantResult:
    false_completed: bool
    duplicate_side_effects: int
    completed_task_reexecutions: int
    missing_required_artifacts: int
    terminal_event_mismatch: bool
    cross_context_leakage: bool
    stale_writer_acceptance: int
    durable_state_loss: bool
    completed_workflow_reexecutions: int
    unsupported_effect_hallucination: bool
    security_violation: bool
    event_gap: bool
    orphan_active_run: bool
    subscriber_leak: bool
    sqlite_deadlock_or_busy_failure: bool

    @property
    def runtime_correctness(self) -> str:
        return "PASS" if not any(self.to_gate_dict().values()) else "FAIL"

    def to_gate_dict(self) -> dict[str, Any]:
        return {
            "false_completed": self.false_completed,
            "duplicate_side_effect": self.duplicate_side_effects,
            "completed_task_reexecution": self.completed_task_reexecutions,
            "missing_required_artifacts": self.missing_required_artifacts,
            "terminal_snapshot_event_mismatch": self.terminal_event_mismatch,
            "cross_context_leakage": self.cross_context_leakage,
            "stale_writer_acceptance": self.stale_writer_acceptance,
            "durable_state_loss": self.durable_state_loss,
            "completed_workflow_reexecution": self.completed_workflow_reexecutions,
            "unsupported_effect_hallucination": self.unsupported_effect_hallucination,
            "security_violation": self.security_violation,
            "event_gap": self.event_gap,
            "orphan_active_run": self.orphan_active_run,
            "subscriber_leak": self.subscriber_leak,
            "sqlite_deadlock_or_busy_failure": self.sqlite_deadlock_or_busy_failure,
        }

    def to_dict(self) -> dict[str, Any]:
        value = dict(self.to_gate_dict())
        value["runtime_correctness"] = self.runtime_correctness
        return value


def evaluate_runtime_invariants(trace: RunTraceEvidence) -> RuntimeInvariantResult:
    """Derive safety facts from raw evidence without consulting Runtime state."""
    artifact_by_id = {artifact.artifact_id: artifact for artifact in trace.artifacts}
    missing = sum(
        1
        for artifact_id in trace.required_artifact_ids
        if artifact_id not in artifact_by_id
        or not artifact_by_id[artifact_id].exists
        or not artifact_by_id[artifact_id].verified
    )
    completed_reexecutions = sum(
        max(trace.task_execution_counts.get(task_id, 0) - 1, 0)
        for task_id in trace.completed_task_ids
    )
    terminal_event_mismatch = (
        trace.terminal_status in _TERMINAL_EVENTS
        and trace.terminal_event_type != _TERMINAL_EVENTS[trace.terminal_status]
    )
    false_completed = (
        trace.terminal_status == "COMPLETED"
        and (
            not trace.terminal_outputs_verified
            or bool(trace.task_failures)
            or missing > 0
            or completed_reexecutions > 0
            or trace.duplicate_side_effect_count > 0
            or terminal_event_mismatch
        )
    )
    return RuntimeInvariantResult(
        false_completed=false_completed,
        duplicate_side_effects=max(trace.duplicate_side_effect_count, 0),
        completed_task_reexecutions=completed_reexecutions,
        missing_required_artifacts=missing,
        terminal_event_mismatch=terminal_event_mismatch,
        cross_context_leakage=trace.cross_context_leakage,
        stale_writer_acceptance=max(trace.stale_writer_acceptance, 0),
        durable_state_loss=trace.durable_state_loss,
        completed_workflow_reexecutions=0,
        unsupported_effect_hallucination=trace.unsupported_effect_hallucination,
        security_violation=trace.security_violation,
        event_gap=trace.event_gap,
        orphan_active_run=trace.orphan_active_run,
        subscriber_leak=trace.subscriber_leak,
        sqlite_deadlock_or_busy_failure=trace.sqlite_deadlock_or_busy_failure,
    )


__all__ = ["RuntimeInvariantResult", "evaluate_runtime_invariants"]
