"""Deterministic P2 acceptance cases.

This module defines the evidence contract only. It does not start a Run, call
a Provider, kill a process, or claim that P2 Runtime Endurance is implemented.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, cast


BENCHMARK_NAME = "runtime-endurance-portability-v2.3-p2"
BENCHMARK_VERSION = "v0.1"
CONTRACT_VERSION = "adr-0022-v1"


class P2Group(str, Enum):
    LONG_HORIZON = "long_horizon"
    RESTART = "restart_recovery"
    SOAK = "soak_concurrency"
    PORTABILITY = "provider_portability"


class ValidationMode(str, Enum):
    DETERMINISTIC = "deterministic"
    SUBPROCESS = "subprocess"
    SOAK = "soak"
    REAL_PROVIDER = "real_provider"


class CapabilityTarget(str, Enum):
    MEASURE_ONLY = "MEASURE_ONLY"
    REQUIRED_PASS = "REQUIRED_PASS"


class RuntimeExpectation(str, Enum):
    PASS = "PASS"


HARD_GATES = frozenset(
    {
        "false_completed",
        "duplicate_side_effect",
        "cross_context_leakage",
        "security_violation",
        "stale_writer_acceptance",
        "terminal_snapshot_event_mismatch",
        "durable_state_loss",
        "completed_workflow_reexecution",
        "unsupported_effect_hallucination",
        "event_gap",
        "orphan_active_run",
        "subscriber_leak",
        "sqlite_deadlock_or_busy_failure",
    }
)

PERFORMANCE_METRICS = (
    "wall_ms",
    "provider_ms",
    "llm_calls",
    "replans",
    "tool_calls",
    "time_to_first_event_ms",
    "time_to_first_artifact_ms",
)


def _unique(values: tuple[str, ...], label: str) -> tuple[str, ...]:
    normalized = tuple(str(value).strip() for value in values if str(value).strip())
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{label} must not contain duplicates")
    return normalized


@dataclass(frozen=True)
class P2Case:
    id: str
    group: P2Group
    mode: ValidationMode
    title: str
    scenario: str
    capability_target: CapabilityTarget
    runtime_expectation: RuntimeExpectation
    required_runtime_evidence: tuple[str, ...]
    hard_gates: tuple[str, ...]
    performance_profile: str
    performance_metrics: tuple[str, ...]
    description: str
    provider_parity_key: str = ""
    provider_variants: tuple[str, ...] = ()
    reprompt_allowed: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "group", P2Group(self.group))
        object.__setattr__(self, "mode", ValidationMode(self.mode))
        object.__setattr__(self, "capability_target", CapabilityTarget(self.capability_target))
        object.__setattr__(self, "runtime_expectation", RuntimeExpectation(self.runtime_expectation))
        for field_name in (
            "required_runtime_evidence",
            "hard_gates",
            "performance_metrics",
            "provider_variants",
        ):
            object.__setattr__(self, field_name, _unique(getattr(self, field_name), field_name))
        if not self.id.strip() or not self.title.strip() or not self.scenario.strip():
            raise ValueError("P2 case requires id, title, and scenario")
        if not self.required_runtime_evidence:
            raise ValueError(f"{self.id}: required_runtime_evidence must not be empty")
        if not self.hard_gates:
            raise ValueError(f"{self.id}: hard_gates must not be empty")
        if not self.performance_profile.strip():
            raise ValueError(f"{self.id}: performance_profile must not be empty")
        if not set(self.hard_gates).issubset(HARD_GATES):
            unknown = sorted(set(self.hard_gates) - HARD_GATES)
            raise ValueError(f"{self.id}: unknown hard gates: {unknown}")
        if not set(self.performance_metrics).issubset(PERFORMANCE_METRICS):
            unknown = sorted(set(self.performance_metrics) - set(PERFORMANCE_METRICS))
            raise ValueError(f"{self.id}: unknown performance metrics: {unknown}")
        if self.group is P2Group.PORTABILITY:
            if not self.provider_parity_key.strip():
                raise ValueError(f"{self.id}: provider parity key is required")
            if len(self.provider_variants) < 2:
                raise ValueError(f"{self.id}: at least two provider variants are required")
            if self.reprompt_allowed:
                raise ValueError(f"{self.id}: provider comparison must not reprompt")
        elif self.provider_parity_key or self.provider_variants or self.reprompt_allowed:
            raise ValueError(f"{self.id}: provider parity fields are portability-only")

    def to_dict(self, *, include_description: bool = True) -> dict[str, Any]:
        value: dict[str, Any] = {
            "id": self.id,
            "group": self.group.value,
            "mode": self.mode.value,
            "title": self.title,
            "scenario": self.scenario,
            "capability_target": self.capability_target.value,
            "runtime_expectation": self.runtime_expectation.value,
            "required_runtime_evidence": list(self.required_runtime_evidence),
            "hard_gates": list(self.hard_gates),
            "performance_profile": self.performance_profile,
            "performance_metrics": list(self.performance_metrics),
            "provider_parity_key": self.provider_parity_key,
            "provider_variants": list(self.provider_variants),
            "reprompt_allowed": self.reprompt_allowed,
        }
        if include_description:
            value["description"] = self.description
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "P2Case":
        return cls(
            id=str(value.get("id", "")),
            group=P2Group(str(value.get("group", ""))),
            mode=ValidationMode(str(value.get("mode", ""))),
            title=str(value.get("title", "")),
            scenario=str(value.get("scenario", "")),
            capability_target=CapabilityTarget(str(value.get("capability_target", ""))),
            runtime_expectation=RuntimeExpectation(str(value.get("runtime_expectation", ""))),
            required_runtime_evidence=tuple(
                str(item)
                for item in cast(list[Any], value.get("required_runtime_evidence", []) or [])
            ),
            hard_gates=tuple(str(item) for item in cast(list[Any], value.get("hard_gates", []) or [])),
            performance_profile=str(value.get("performance_profile", "")),
            performance_metrics=tuple(
                str(item) for item in cast(list[Any], value.get("performance_metrics", []) or [])
            ),
            description=str(value.get("description", "")),
            provider_parity_key=str(value.get("provider_parity_key", "")),
            provider_variants=tuple(
                str(item) for item in cast(list[Any], value.get("provider_variants", []) or [])
            ),
            reprompt_allowed=bool(value.get("reprompt_allowed", False)),
        )


_COMMON_HARD_GATES = (
    "false_completed",
    "duplicate_side_effect",
    "terminal_snapshot_event_mismatch",
    "durable_state_loss",
)


def _case(
    case_id: str,
    group: P2Group,
    mode: ValidationMode,
    title: str,
    scenario: str,
    evidence: tuple[str, ...],
    gates: tuple[str, ...],
    profile: str,
    metrics: tuple[str, ...],
    description: str,
    *,
    parity_key: str = "",
    variants: tuple[str, ...] = (),
) -> P2Case:
    return P2Case(
        id=case_id,
        group=group,
        mode=mode,
        title=title,
        scenario=scenario,
        capability_target=CapabilityTarget.MEASURE_ONLY,
        runtime_expectation=RuntimeExpectation.PASS,
        required_runtime_evidence=evidence,
        hard_gates=gates,
        performance_profile=profile,
        performance_metrics=metrics,
        description=description,
        provider_parity_key=parity_key,
        provider_variants=variants,
    )


def build_cases() -> tuple[P2Case, ...]:
    """Return the frozen 5/4/4/3 P2 acceptance manifest."""
    return (
        _case(
            "L01", P2Group.LONG_HORIZON, ValidationMode.REAL_PROVIDER,
            "ten-step dependency chain", "10-step natural dependency chain",
            ("task_lineage", "completed_task_ids", "artifact_digests", "terminal_verifier"),
            _COMMON_HARD_GATES + ("completed_workflow_reexecution",), "long_horizon",
            PERFORMANCE_METRICS,
            "Run a natural ten-step chain and measure useful progress without requiring every model answer to be correct.",
        ),
        _case(
            "L02", P2Group.LONG_HORIZON, ValidationMode.REAL_PROVIDER,
            "branch and join", "12-step branch/join plan with shared artifacts",
            ("task_lineage", "dependency_edges", "artifact_digests", "required_outputs", "terminal_verifier"),
            _COMMON_HARD_GATES + ("completed_workflow_reexecution",), "long_horizon",
            PERFORMANCE_METRICS,
            "Verify that completed branches are preserved and the join does not re-execute their side effects.",
        ),
        _case(
            "L03", P2Group.LONG_HORIZON, ValidationMode.REAL_PROVIDER,
            "recoverable failure with bounded replan", "inject one recoverable task failure in a 10-step chain",
            ("failure_event", "replan_count", "task_lineage", "terminal_status", "execution_truth"),
            _COMMON_HARD_GATES + ("completed_workflow_reexecution",), "long_horizon",
            PERFORMANCE_METRICS,
            "Measure progress preservation and prove that replan is bounded rather than an unobserved loop.",
        ),
        _case(
            "L04", P2Group.LONG_HORIZON, ValidationMode.REAL_PROVIDER,
            "multi-artifact completion", "one long task requiring three verified outputs",
            ("required_outputs", "artifact_digests", "verifier_evidence", "terminal_event", "run_snapshot"),
            _COMMON_HARD_GATES, "long_horizon",
            PERFORMANCE_METRICS,
            "All required artifacts must be real and verified; partial output must not become COMPLETED.",
        ),
        _case(
            "L05", P2Group.LONG_HORIZON, ValidationMode.REAL_PROVIDER,
            "interrupted long chain", "interrupt after useful progress and resume the remaining chain",
            ("checkpoint_id", "completed_task_ids", "resume_decision", "skipped_side_effects", "terminal_verifier"),
            _COMMON_HARD_GATES + ("completed_workflow_reexecution",), "resume",
            PERFORMANCE_METRICS,
            "Resume from a real checkpoint and report useful progress preserved rather than restarting from zero.",
        ),
        _case(
            "R01", P2Group.RESTART, ValidationMode.SUBPROCESS,
            "kill active run", "kill the worker while a Run is ACTIVE, then resume it",
            ("subprocess_exit", "durable_checkpoint", "resume_decision", "execution_counts", "terminal_event"),
            _COMMON_HARD_GATES + ("stale_writer_acceptance", "completed_workflow_reexecution"), "resume",
            PERFORMANCE_METRICS,
            "The new process must reconstruct the Run and continue without rerunning completed effects.",
        ),
        _case(
            "R02", P2Group.RESTART, ValidationMode.SUBPROCESS,
            "effect committed before finalize", "kill after an external effect commits but before finalization",
            ("prepared_intent", "effect_ledger", "external_reference", "reconcile_decision", "execution_counts"),
            ("false_completed", "duplicate_side_effect", "stale_writer_acceptance", "durable_state_loss"), "resume",
            PERFORMANCE_METRICS,
            "Reconcile the committed effect exactly once; never infer a second external call from a lost response.",
        ),
        _case(
            "R03", P2Group.RESTART, ValidationMode.SUBPROCESS,
            "event committed before client response", "commit checkpoint and event, then kill before client receives it",
            ("checkpoint_id", "event_id", "sequence_number", "after_sequence_replay", "execution_counts"),
            ("event_gap", "duplicate_side_effect", "terminal_snapshot_event_mismatch", "durable_state_loss"), "event_replay",
            PERFORMANCE_METRICS,
            "Replay must contain the committed event exactly once from the cursor without re-executing the Run.",
        ),
        _case(
            "R04", P2Group.RESTART, ValidationMode.SUBPROCESS,
            "completed A and active B", "Workflow A is complete while B is active at process kill",
            ("completed_workflow_ids", "active_workflow_id", "active_checkpoint_id", "execution_counts", "artifact_digests"),
            _COMMON_HARD_GATES + ("completed_workflow_reexecution", "stale_writer_acceptance"), "resume",
            PERFORMANCE_METRICS,
            "Only B may resume; A's artifacts and side effects remain unchanged.",
        ),
        _case(
            "S01", P2Group.SOAK, ValidationMode.SOAK,
            "50 sequential runs", "execute 50 isolated sequential Runs in one process",
            ("run_count", "terminal_statuses", "connection_count", "pending_task_count", "context_count"),
            ("cross_context_leakage", "duplicate_side_effect", "orphan_active_run", "subscriber_leak", "sqlite_deadlock_or_busy_failure"), "simple",
            ("wall_ms", "provider_ms", "llm_calls", "tool_calls"),
            "Track resource and identity stability across sequential creation, completion, and close cycles.",
        ),
        _case(
            "S02", P2Group.SOAK, ValidationMode.SOAK,
            "ten sessions by five runs", "10 sessions each execute 5 Runs with shared provider access",
            ("session_run_matrix", "artifact_scope", "event_scope", "memory_scope", "terminal_statuses"),
            ("cross_context_leakage", "duplicate_side_effect", "subscriber_leak", "sqlite_deadlock_or_busy_failure"), "multi_tool",
            ("wall_ms", "provider_ms", "llm_calls", "tool_calls"),
            "Prove Session, Run, Artifact, Event, and Memory scopes remain isolated under repeated use.",
        ),
        _case(
            "S03", P2Group.SOAK, ValidationMode.SOAK,
            "ten concurrent runs", "run 10 Runs concurrently with forced interleaving",
            ("barrier_interleaving", "artifact_scope", "event_scope", "execution_counts", "terminal_statuses"),
            ("cross_context_leakage", "duplicate_side_effect", "orphan_active_run", "subscriber_leak", "sqlite_deadlock_or_busy_failure"), "multi_tool",
            ("wall_ms", "provider_ms", "llm_calls", "tool_calls", "time_to_first_event_ms"),
            "Use barriers rather than sequential calls so same-key artifact and event collisions are observable.",
        ),
        _case(
            "S04", P2Group.SOAK, ValidationMode.SOAK,
            "500 event replay cycles", "perform 500 durable event replay/read cycles with reconnects",
            ("sequence_numbers", "cursor_positions", "event_ids", "cursor_expiry_results", "subscriber_count"),
            ("event_gap", "cross_context_leakage", "subscriber_leak", "terminal_snapshot_event_mismatch"), "event_replay",
            ("wall_ms", "time_to_first_event_ms"),
            "Measure replay correctness and resource stability; slow readers must not block the Runtime.",
        ),
        _case(
            "P01", P2Group.PORTABILITY, ValidationMode.REAL_PROVIDER,
            "simple tool parity", "same simple filesystem/tool task on two Providers",
            ("provider_identity", "tool_calls", "execution_truth", "artifact_digests", "terminal_status"),
            _COMMON_HARD_GATES + ("unsupported_effect_hallucination",), "single_tool",
            PERFORMANCE_METRICS,
            "Run the same scenario on both Providers; do not reprompt or change the requested task.",
            parity_key="simple-tool", variants=("primary", "secondary"),
        ),
        _case(
            "P02", P2Group.PORTABILITY, ValidationMode.REAL_PROVIDER,
            "multi-goal tool parity", "same multi-goal/multi-tool task on two Providers",
            ("provider_identity", "tool_calls", "required_outputs", "artifact_digests", "execution_truth", "terminal_status"),
            _COMMON_HARD_GATES + ("unsupported_effect_hallucination",), "multi_tool",
            PERFORMANCE_METRICS,
            "Compare capability outcome while requiring identical Runtime grounding and terminal semantics.",
            parity_key="multi-goal-tool", variants=("primary", "secondary"),
        ),
        _case(
            "P03", P2Group.PORTABILITY, ValidationMode.REAL_PROVIDER,
            "unsupported effect and malformed response", "same unsupported-effect request plus malformed structured-response probe",
            ("provider_identity", "capability_resolution", "tool_calls", "stable_error", "terminal_event", "run_snapshot"),
            ("false_completed", "unsupported_effect_hallucination", "terminal_snapshot_event_mismatch", "security_violation"), "single_tool",
            PERFORMANCE_METRICS,
            "A weaker Provider may fail the capability, but both Providers must fail safely without external action or success claim.",
            parity_key="unsupported-malformed", variants=("primary", "secondary"),
        ),
    )


__all__ = [
    "BENCHMARK_NAME",
    "BENCHMARK_VERSION",
    "CONTRACT_VERSION",
    "CapabilityTarget",
    "HARD_GATES",
    "P2Case",
    "P2Group",
    "PERFORMANCE_METRICS",
    "RuntimeExpectation",
    "ValidationMode",
    "build_cases",
]
