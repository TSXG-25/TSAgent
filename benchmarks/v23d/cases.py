"""Failure-oriented v2.3D Cancellation / Timeout Contract Dataset."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, cast

from agent.interruption import InterruptionReason, CancellationSafetyClass


BENCHMARK_NAME = "cancellation-timeout-contract-v2.3d"
BENCHMARK_VERSION = "v0.1"
CONTRACT_VERSION = "adr-0023-v1"


class D1Group(str, Enum):
    REQUEST_LIFECYCLE = "request_lifecycle"
    SAFE_BOUNDARY = "safe_boundary"
    RESTART_RESUME = "restart_resume"
    TIMEOUT_POLICY = "timeout_policy"
    CLIENT_LIFECYCLE = "client_lifecycle"
    COMMITTED_EFFECT = "committed_effect"


class Probe(str, Enum):
    CANCEL_BEFORE_FIRST_TOOL = "CANCEL_BEFORE_FIRST_TOOL"
    CANCEL_DURING_PROVIDER_WAIT = "CANCEL_DURING_PROVIDER_WAIT"
    CANCEL_BEFORE_FILESYSTEM_WRITE = "CANCEL_BEFORE_FILESYSTEM_WRITE"
    CANCEL_AFTER_EFFECT_COMMIT = "CANCEL_AFTER_EFFECT_COMMIT"
    CANCEL_DURING_FINALIZATION = "CANCEL_DURING_FINALIZATION"
    DUPLICATE_CANCEL_REQUEST = "DUPLICATE_CANCEL_REQUEST"
    CANCEL_COMPLETED_RUN = "CANCEL_COMPLETED_RUN"
    CANCEL_ALREADY_CANCELLED = "CANCEL_ALREADY_CANCELLED"
    PROCESS_DIES_AFTER_INTENT = "PROCESS_DIES_AFTER_INTENT"
    NEW_WORKER_OBSERVES_CANCEL = "NEW_WORKER_OBSERVES_CANCEL"
    RUN_TIMEOUT = "RUN_TIMEOUT"
    TOOL_TIMEOUT_DELEGATED = "TOOL_TIMEOUT_DELEGATED"
    CANCEL_MULTI_WORKFLOW = "CANCEL_MULTI_WORKFLOW"
    CANCEL_WITH_CLIENT_DISCONNECT = "CANCEL_WITH_CLIENT_DISCONNECT"
    STALE_WRITER_AFTER_CANCEL = "STALE_WRITER_AFTER_CANCEL"
    EXTERNAL_COMMITTED_EFFECT = "EXTERNAL_COMMITTED_EFFECT"


class ExpectedOutcome(str, Enum):
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"
    DELEGATED = "DELEGATED"
    REJECTED = "REJECTED"
    IDEMPOTENT = "IDEMPOTENT"


HARD_GATES = frozenset(
    {
        "post_cancel_new_side_effect",
        "duplicate_cancel_transition",
        "false_cancelled_before_durable_flush",
        "completed_effect_silently_lost",
        "cancelled_run_auto_resumed",
        "atomic_transaction_torn_by_cancellation",
        "terminal_snapshot_event_mismatch",
        "stale_writer_after_cancel_accepted",
        "cancel_intent_lost_after_restart",
        "timeout_misclassified_as_completed",
    }
)


PERFORMANCE_METRICS = (
    "cancel_request_to_cancelling_ms",
    "cancelling_to_terminal_ms",
    "provider_cancellation_ms",
    "tool_safe_boundary_ms",
)


def _unique(values: tuple[str, ...], label: str) -> tuple[str, ...]:
    normalized = tuple(str(value).strip() for value in values if str(value).strip())
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{label} must not contain duplicates")
    return normalized


@dataclass(frozen=True)
class InterruptionContractCase:
    id: str
    group: D1Group
    probe: Probe
    reason: InterruptionReason
    safety_class: CancellationSafetyClass
    expected_outcome: ExpectedOutcome
    required_evidence: tuple[str, ...]
    hard_gates: tuple[str, ...]
    must_preserve: tuple[str, ...]
    must_not: tuple[str, ...]
    description: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "group", D1Group(self.group))
        object.__setattr__(self, "probe", Probe(self.probe))
        object.__setattr__(self, "reason", InterruptionReason(self.reason))
        object.__setattr__(
            self, "safety_class", CancellationSafetyClass(self.safety_class)
        )
        object.__setattr__(self, "expected_outcome", ExpectedOutcome(self.expected_outcome))
        for field_name in ("required_evidence", "hard_gates", "must_preserve", "must_not"):
            object.__setattr__(self, field_name, _unique(getattr(self, field_name), field_name))
        if not self.id.strip() or not self.description.strip():
            raise ValueError("interruption case requires id and description")
        if not self.required_evidence or not self.hard_gates or not self.must_not:
            raise ValueError(f"{self.id}: evidence, hard_gates, and must_not are required")
        unknown = set(self.hard_gates) - HARD_GATES
        if unknown:
            raise ValueError(f"{self.id}: unknown hard gates: {sorted(unknown)}")

    def to_dict(self, *, include_description: bool = True) -> dict[str, Any]:
        value: dict[str, Any] = {
            "id": self.id,
            "group": self.group.value,
            "probe": self.probe.value,
            "reason": self.reason.value,
            "safety_class": self.safety_class.value,
            "expected_outcome": self.expected_outcome.value,
            "required_evidence": list(self.required_evidence),
            "hard_gates": list(self.hard_gates),
            "must_preserve": list(self.must_preserve),
            "must_not": list(self.must_not),
        }
        if include_description:
            value["description"] = self.description
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "InterruptionContractCase":
        return cls(
            id=str(value.get("id", "")),
            group=D1Group(str(value.get("group", ""))),
            probe=Probe(str(value.get("probe", ""))),
            reason=InterruptionReason(str(value.get("reason", ""))),
            safety_class=CancellationSafetyClass(str(value.get("safety_class", ""))),
            expected_outcome=ExpectedOutcome(str(value.get("expected_outcome", ""))),
            required_evidence=tuple(
                str(item) for item in cast(list[Any], value.get("required_evidence", []) or [])
            ),
            hard_gates=tuple(
                str(item) for item in cast(list[Any], value.get("hard_gates", []) or [])
            ),
            must_preserve=tuple(
                str(item) for item in cast(list[Any], value.get("must_preserve", []) or [])
            ),
            must_not=tuple(
                str(item) for item in cast(list[Any], value.get("must_not", []) or [])
            ),
            description=str(value.get("description", "")),
        )


_CANCEL_GATES = (
    "post_cancel_new_side_effect",
    "false_cancelled_before_durable_flush",
    "terminal_snapshot_event_mismatch",
)


def _case(
    case_id: str,
    group: D1Group,
    probe: Probe,
    reason: InterruptionReason,
    safety: CancellationSafetyClass,
    outcome: ExpectedOutcome,
    evidence: tuple[str, ...],
    gates: tuple[str, ...],
    preserve: tuple[str, ...],
    must_not: tuple[str, ...],
    description: str,
) -> InterruptionContractCase:
    return InterruptionContractCase(
        id=case_id,
        group=group,
        probe=probe,
        reason=reason,
        safety_class=safety,
        expected_outcome=outcome,
        required_evidence=evidence,
        hard_gates=gates,
        must_preserve=preserve,
        must_not=must_not,
        description=description,
    )


def build_cases() -> tuple[InterruptionContractCase, ...]:
    """Return the frozen C01-C16 failure-oriented manifest."""

    boundary = CancellationSafetyClass.BOUNDARY_ONLY
    user_cancel = InterruptionReason.USER_CANCEL
    return (
        _case(
            "C01", D1Group.REQUEST_LIFECYCLE, Probe.CANCEL_BEFORE_FIRST_TOOL,
            user_cancel, boundary, ExpectedOutcome.CANCELLED,
            ("durable_intent", "run_cancelling", "run_cancelled"),
            _CANCEL_GATES, (), ("tool_start", "side_effect"),
            "Cancel before the first Tool; no Tool or side effect may start.",
        ),
        _case(
            "C02", D1Group.SAFE_BOUNDARY, Probe.CANCEL_DURING_PROVIDER_WAIT,
            user_cancel, CancellationSafetyClass.INTERRUPTIBLE, ExpectedOutcome.CANCELLED,
            ("durable_intent", "provider_cancel_evidence", "run_cancelled"),
            _CANCEL_GATES, (), ("new_task", "run_completed"),
            "An interruptible Provider wait may stop after the durable request is visible.",
        ),
        _case(
            "C03", D1Group.SAFE_BOUNDARY, Probe.CANCEL_BEFORE_FILESYSTEM_WRITE,
            user_cancel, boundary, ExpectedOutcome.CANCELLED,
            ("durable_intent", "before_tool_boundary", "run_cancelled"),
            _CANCEL_GATES, (), ("filesystem_write", "artifact_commit"),
            "Cancel at the boundary before a filesystem write.",
        ),
        _case(
            "C04", D1Group.SAFE_BOUNDARY, Probe.CANCEL_AFTER_EFFECT_COMMIT,
            user_cancel, CancellationSafetyClass.NON_CANCELLABLE_ONCE_COMMITTED,
            ExpectedOutcome.CANCELLED,
            ("durable_intent", "committed_effect", "run_cancelled"),
            _CANCEL_GATES + ("completed_effect_silently_lost",),
            ("effect_evidence", "artifact_digest"), ("effect_replay", "rollback_claim"),
            "A committed effect remains true while subsequent work is cancelled.",
        ),
        _case(
            "C05", D1Group.SAFE_BOUNDARY, Probe.CANCEL_DURING_FINALIZATION,
            user_cancel, boundary, ExpectedOutcome.CANCELLED,
            ("durable_intent", "finalization_commit", "post_commit_boundary"),
            _CANCEL_GATES + ("atomic_transaction_torn_by_cancellation",),
            ("finalization_bundle",), ("partial_transaction", "premature_cancelled"),
            "Finalization Bundle is indivisible; cancellation waits for its safe boundary.",
        ),
        _case(
            "C06", D1Group.REQUEST_LIFECYCLE, Probe.DUPLICATE_CANCEL_REQUEST,
            user_cancel, boundary, ExpectedOutcome.IDEMPOTENT,
            ("request_digest", "intent_identity", "single_transition"),
            ("duplicate_cancel_transition",), ("original_intent",),
            ("second_intent", "second_terminal_event"),
            "The same request id and digest return the same durable intent.",
        ),
        _case(
            "C07", D1Group.REQUEST_LIFECYCLE, Probe.CANCEL_COMPLETED_RUN,
            user_cancel, boundary, ExpectedOutcome.REJECTED,
            ("completed_snapshot", "rejection_reason"),
            ("terminal_snapshot_event_mismatch",), ("completed_terminal",),
            ("run_cancelling", "run_cancelled"),
            "A completed Run cannot be rewritten as cancelled.",
        ),
        _case(
            "C08", D1Group.REQUEST_LIFECYCLE, Probe.CANCEL_ALREADY_CANCELLED,
            user_cancel, boundary, ExpectedOutcome.IDEMPOTENT,
            ("cancelled_snapshot", "original_terminal_event"),
            ("duplicate_cancel_transition",), ("original_cancel_fact",),
            ("second_terminal_event", "new_side_effect"),
            "Cancelling an already cancelled Run is an idempotent read of existing truth.",
        ),
        _case(
            "C09", D1Group.RESTART_RESUME, Probe.PROCESS_DIES_AFTER_INTENT,
            user_cancel, boundary, ExpectedOutcome.CANCELLED,
            ("fsynced_intent", "process_exit", "rehydrated_intent"),
            _CANCEL_GATES + ("cancel_intent_lost_after_restart",),
            ("intent_revision",), ("intent_loss", "automatic_execution_resume"),
            "A process death after intent commit cannot erase the cancellation request.",
        ),
        _case(
            "C10", D1Group.RESTART_RESUME, Probe.NEW_WORKER_OBSERVES_CANCEL,
            user_cancel, boundary, ExpectedOutcome.CANCELLED,
            ("rehydrated_intent", "new_writer_fence", "run_cancelled"),
            _CANCEL_GATES + ("cancelled_run_auto_resumed",),
            ("completed_effects",), ("workflow_resume", "new_task"),
            "A new worker observes the durable request and does not resume execution.",
        ),
        _case(
            "C11", D1Group.TIMEOUT_POLICY, Probe.RUN_TIMEOUT,
            InterruptionReason.RUN_TIMEOUT, CancellationSafetyClass.INTERRUPTIBLE,
            ExpectedOutcome.TIMED_OUT,
            ("timeout_intent", "safe_boundary", "run_timed_out"),
            ("timeout_misclassified_as_completed", "terminal_snapshot_event_mismatch"),
            (), ("run_completed", "automatic_resume"),
            "Run timeout is terminal TIMED_OUT after durable flush, never COMPLETED.",
        ),
        _case(
            "C12", D1Group.TIMEOUT_POLICY, Probe.TOOL_TIMEOUT_DELEGATED,
            InterruptionReason.TOOL_TIMEOUT, CancellationSafetyClass.INTERRUPTIBLE,
            ExpectedOutcome.DELEGATED,
            ("tool_timeout", "decision_policy_input"),
            ("timeout_misclassified_as_completed",), (),
            ("automatic_run_timed_out", "run_completed"),
            "Tool timeout is Decision-owned and does not automatically time out the Run.",
        ),
        _case(
            "C13", D1Group.RESTART_RESUME, Probe.CANCEL_MULTI_WORKFLOW,
            user_cancel, boundary, ExpectedOutcome.CANCELLED,
            ("workflow_a_completed", "workflow_b_active", "durable_intent"),
            _CANCEL_GATES + ("completed_effect_silently_lost",),
            ("workflow_a_artifacts", "workflow_a_completion"),
            ("workflow_a_reexecution", "workflow_c_activation"),
            "Cancel after Workflow A completed and while Workflow B is active.",
        ),
        _case(
            "C14", D1Group.CLIENT_LIFECYCLE, Probe.CANCEL_WITH_CLIENT_DISCONNECT,
            user_cancel, CancellationSafetyClass.INTERRUPTIBLE, ExpectedOutcome.CANCELLED,
            ("durable_intent", "client_disconnect", "terminal_event"),
            _CANCEL_GATES, ("intent_after_disconnect",),
            ("intent_loss", "client_owned_runtime"),
            "Client disconnect does not own or erase an already durable cancel request.",
        ),
        _case(
            "C15", D1Group.RESTART_RESUME, Probe.STALE_WRITER_AFTER_CANCEL,
            user_cancel, boundary, ExpectedOutcome.REJECTED,
            ("old_fence", "new_fence", "stale_write_rejection"),
            ("stale_writer_after_cancel_accepted",), ("cancel_intent",),
            ("stale_checkpoint", "stale_terminal_event"),
            "An old worker cannot write after cancellation fencing advances.",
        ),
        _case(
            "C16", D1Group.COMMITTED_EFFECT, Probe.EXTERNAL_COMMITTED_EFFECT,
            user_cancel, CancellationSafetyClass.NON_CANCELLABLE_ONCE_COMMITTED,
            ExpectedOutcome.CANCELLED,
            ("external_effect_reference", "committed_evidence", "run_cancelled"),
            _CANCEL_GATES + ("completed_effect_silently_lost",),
            ("external_effect_evidence",), ("rollback_fiction", "duplicate_effect"),
            "Cancellation preserves a committed external effect and stops later effects.",
        ),
    )


__all__ = [
    "BENCHMARK_NAME",
    "BENCHMARK_VERSION",
    "CONTRACT_VERSION",
    "D1Group",
    "ExpectedOutcome",
    "HARD_GATES",
    "InterruptionContractCase",
    "PERFORMANCE_METRICS",
    "Probe",
    "build_cases",
]
