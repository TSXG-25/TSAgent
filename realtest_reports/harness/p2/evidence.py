"""Serializable evidence model shared by P2 acceptance harness groups."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


def _string_tuple(values: Any) -> tuple[str, ...]:
    if values is None:
        return ()
    return tuple(str(item) for item in values if str(item).strip())


def _string_int_map(value: Any) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, int] = {}
    for key, raw in value.items():
        try:
            result[str(key)] = int(raw)
        except (TypeError, ValueError):
            result[str(key)] = 0
    return result


@dataclass(frozen=True)
class ArtifactEvidence:
    artifact_id: str
    digest: str
    verified: bool
    exists: bool = True
    producer: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "digest": self.digest,
            "verified": self.verified,
            "exists": self.exists,
            "producer": self.producer,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ArtifactEvidence":
        return cls(
            artifact_id=str(value.get("artifact_id", "")),
            digest=str(value.get("digest", "")),
            verified=bool(value.get("verified", False)),
            exists=bool(value.get("exists", True)),
            producer=str(value.get("producer", "")),
        )


@dataclass(frozen=True)
class PerformanceEvidence:
    wall_ms: float = 0.0
    provider_ms: float = 0.0
    llm_calls: int = 0
    replans: int = 0
    tool_calls_count: int = 0
    time_to_first_event_ms: float = 0.0
    time_to_first_artifact_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "wall_ms": self.wall_ms,
            "provider_ms": self.provider_ms,
            "llm_calls": self.llm_calls,
            "replans": self.replans,
            "tool_calls_count": self.tool_calls_count,
            "time_to_first_event_ms": self.time_to_first_event_ms,
            "time_to_first_artifact_ms": self.time_to_first_artifact_ms,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PerformanceEvidence":
        return cls(
            wall_ms=float(value.get("wall_ms", 0.0)),
            provider_ms=float(value.get("provider_ms", 0.0)),
            llm_calls=int(value.get("llm_calls", 0)),
            replans=int(value.get("replans", 0)),
            tool_calls_count=int(value.get("tool_calls_count", 0)),
            time_to_first_event_ms=float(value.get("time_to_first_event_ms", 0.0)),
            time_to_first_artifact_ms=float(value.get("time_to_first_artifact_ms", 0.0)),
        )


@dataclass(frozen=True)
class RunTraceEvidence:
    """Raw Run facts; no pass/fail decision is stored in this object."""

    case_id: str
    run_id: str
    provider: str
    planned_tasks: tuple[str, ...]
    workflow_transitions: tuple[str, ...]
    task_execution_counts: dict[str, int]
    completed_task_ids: tuple[str, ...]
    artifacts: tuple[ArtifactEvidence, ...]
    required_artifact_ids: tuple[str, ...]
    terminal_status: str
    terminal_event_type: str
    terminal_outputs_verified: bool
    task_failures: tuple[str, ...] = ()
    duplicate_side_effect_count: int = 0
    cross_context_leakage: bool = False
    stale_writer_acceptance: int = 0
    durable_state_loss: bool = False
    unsupported_effect_hallucination: bool = False
    security_violation: bool = False
    event_gap: bool = False
    orphan_active_run: bool = False
    subscriber_leak: bool = False
    sqlite_deadlock_or_busy_failure: bool = False
    provider_errors: tuple[str, ...] = ()
    performance: PerformanceEvidence = PerformanceEvidence()

    def __post_init__(self) -> None:
        object.__setattr__(self, "planned_tasks", _string_tuple(self.planned_tasks))
        object.__setattr__(self, "workflow_transitions", _string_tuple(self.workflow_transitions))
        object.__setattr__(self, "completed_task_ids", _string_tuple(self.completed_task_ids))
        object.__setattr__(self, "required_artifact_ids", _string_tuple(self.required_artifact_ids))
        object.__setattr__(self, "task_failures", _string_tuple(self.task_failures))
        object.__setattr__(self, "provider_errors", _string_tuple(self.provider_errors))
        object.__setattr__(self, "task_execution_counts", _string_int_map(self.task_execution_counts))
        object.__setattr__(self, "artifacts", tuple(self.artifacts))
        if not isinstance(self.performance, PerformanceEvidence):
            object.__setattr__(
                self,
                "performance",
                PerformanceEvidence.from_dict(self.performance),
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "run_id": self.run_id,
            "provider": self.provider,
            "planned_tasks": list(self.planned_tasks),
            "workflow_transitions": list(self.workflow_transitions),
            "task_execution_counts": dict(self.task_execution_counts),
            "completed_task_ids": list(self.completed_task_ids),
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "required_artifact_ids": list(self.required_artifact_ids),
            "terminal_status": self.terminal_status,
            "terminal_event_type": self.terminal_event_type,
            "terminal_outputs_verified": self.terminal_outputs_verified,
            "task_failures": list(self.task_failures),
            "duplicate_side_effect_count": self.duplicate_side_effect_count,
            "cross_context_leakage": self.cross_context_leakage,
            "stale_writer_acceptance": self.stale_writer_acceptance,
            "durable_state_loss": self.durable_state_loss,
            "unsupported_effect_hallucination": self.unsupported_effect_hallucination,
            "security_violation": self.security_violation,
            "event_gap": self.event_gap,
            "orphan_active_run": self.orphan_active_run,
            "subscriber_leak": self.subscriber_leak,
            "sqlite_deadlock_or_busy_failure": self.sqlite_deadlock_or_busy_failure,
            "provider_errors": list(self.provider_errors),
            "performance": self.performance.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RunTraceEvidence":
        raw_artifacts = value.get("artifacts", []) or []
        artifacts = tuple(
            ArtifactEvidence.from_dict(item)
            for item in raw_artifacts
            if isinstance(item, Mapping)
        )
        raw_performance = value.get("performance", {})
        performance = (
            raw_performance
            if isinstance(raw_performance, PerformanceEvidence)
            else PerformanceEvidence.from_dict(
                raw_performance if isinstance(raw_performance, Mapping) else {}
            )
        )
        return cls(
            case_id=str(value.get("case_id", "")),
            run_id=str(value.get("run_id", "")),
            provider=str(value.get("provider", "")),
            planned_tasks=_string_tuple(value.get("planned_tasks")),
            workflow_transitions=_string_tuple(value.get("workflow_transitions")),
            task_execution_counts=_string_int_map(value.get("task_execution_counts")),
            completed_task_ids=_string_tuple(value.get("completed_task_ids")),
            artifacts=artifacts,
            required_artifact_ids=_string_tuple(value.get("required_artifact_ids")),
            terminal_status=str(value.get("terminal_status", "")),
            terminal_event_type=str(value.get("terminal_event_type", "")),
            terminal_outputs_verified=bool(value.get("terminal_outputs_verified", False)),
            task_failures=_string_tuple(value.get("task_failures")),
            duplicate_side_effect_count=int(value.get("duplicate_side_effect_count", 0)),
            cross_context_leakage=bool(value.get("cross_context_leakage", False)),
            stale_writer_acceptance=int(value.get("stale_writer_acceptance", 0)),
            durable_state_loss=bool(value.get("durable_state_loss", False)),
            unsupported_effect_hallucination=bool(value.get("unsupported_effect_hallucination", False)),
            security_violation=bool(value.get("security_violation", False)),
            event_gap=bool(value.get("event_gap", False)),
            orphan_active_run=bool(value.get("orphan_active_run", False)),
            subscriber_leak=bool(value.get("subscriber_leak", False)),
            sqlite_deadlock_or_busy_failure=bool(value.get("sqlite_deadlock_or_busy_failure", False)),
            provider_errors=_string_tuple(value.get("provider_errors")),
            performance=performance,
        )


__all__ = ["ArtifactEvidence", "PerformanceEvidence", "RunTraceEvidence"]
