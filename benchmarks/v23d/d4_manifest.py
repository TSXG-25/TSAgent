"""v2.3D-4 Service/CLI cancellation E2E manifest.

This module describes the D4 evidence cases only.  Cancellation semantics
remain defined by the frozen C01-C16 contract in :mod:`benchmarks.v23d.cases`;
the D4 manifest records how those semantics are exercised through public
entry points and real providers.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

from agent.service import EventType, RunStatus


MANIFEST_NAME = "cancellation-timeout-service-e2e-v2.3d-4"
MANIFEST_VERSION = "v0.1"


class ProviderMode(str, Enum):
    DETERMINISTIC = "deterministic"
    REAL_PROVIDER = "real_provider"


@dataclass(frozen=True)
class D4Case:
    """Stable metadata for one D4 service/CLI acceptance scenario."""

    case_id: str
    scenario: str
    provider_mode: ProviderMode
    trigger: str
    expected_terminal_status: RunStatus
    expected_terminal_event: EventType
    required_evidence: tuple[str, ...]
    must_not: tuple[str, ...]
    notes: str

    def __post_init__(self) -> None:
        if not self.case_id.startswith("D4") or len(self.case_id) != 4:
            raise ValueError("D4 case ids must have the form D4nn")
        if not self.scenario.strip() or not self.trigger.strip() or not self.notes.strip():
            raise ValueError(f"{self.case_id}: scenario, trigger, and notes are required")
        object.__setattr__(self, "provider_mode", ProviderMode(self.provider_mode))
        object.__setattr__(
            self,
            "expected_terminal_status",
            RunStatus(self.expected_terminal_status),
        )
        object.__setattr__(
            self,
            "expected_terminal_event",
            EventType(self.expected_terminal_event),
        )
        for field_name in ("required_evidence", "must_not"):
            values = tuple(
                str(value).strip()
                for value in getattr(self, field_name)
                if str(value).strip()
            )
            if not values:
                raise ValueError(f"{self.case_id}: {field_name} must not be empty")
            if len(values) != len(set(values)):
                raise ValueError(f"{self.case_id}: {field_name} must not contain duplicates")
            object.__setattr__(self, field_name, values)

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "scenario": self.scenario,
            "provider_mode": self.provider_mode.value,
            "trigger": self.trigger,
            "expected_terminal_status": self.expected_terminal_status.value,
            "expected_terminal_event": self.expected_terminal_event.value,
            "required_evidence": list(self.required_evidence),
            "must_not": list(self.must_not),
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "D4Case":
        return cls(
            case_id=str(value.get("case_id", "")),
            scenario=str(value.get("scenario", "")),
            provider_mode=ProviderMode(str(value.get("provider_mode", ""))),
            trigger=str(value.get("trigger", "")),
            expected_terminal_status=RunStatus(
                str(value.get("expected_terminal_status", ""))
            ),
            expected_terminal_event=EventType(
                str(value.get("expected_terminal_event", ""))
            ),
            required_evidence=tuple(
                str(item) for item in value.get("required_evidence", ())
            ),
            must_not=tuple(str(item) for item in value.get("must_not", ())),
            notes=str(value.get("notes", "")),
        )


def build_d4_cases() -> tuple[D4Case, ...]:
    """Return the frozen D401-D410 entry-point evidence manifest."""

    cancelled = RunStatus.CANCELLED
    return (
        D4Case(
            "D401", "cancel while Provider is generating", ProviderMode.REAL_PROVIDER,
            "provider_wait", cancelled, EventType.RUN_CANCELLED,
            ("run_cancelling", "provider_wait_aborted", "run_cancelled"),
            ("next_task_started", "provider_fallback", "run_completed"),
            "Real-model wait; assess Runtime cancellation, not server-side token billing.",
        ),
        D4Case(
            "D402", "cancel immediately before the first Tool", ProviderMode.DETERMINISTIC,
            "before_tool", cancelled, EventType.RUN_CANCELLED,
            ("durable_cancel_intent", "tool_execution_count", "run_cancelled"),
            ("tool_started", "side_effect", "run_completed"),
            "The Tool boundary must observe the durable request before execution.",
        ),
        D4Case(
            "D403", "cancel after a committed file effect", ProviderMode.DETERMINISTIC,
            "after_effect_commit", cancelled, EventType.RUN_CANCELLED,
            ("committed_artifact", "artifact_digest", "run_cancelled"),
            ("effect_replay", "artifact_loss", "run_completed"),
            "Facts already committed remain visible; later effects do not start.",
        ),
        D4Case(
            "D404", "cancel a multi-goal Run with A complete and B active", ProviderMode.DETERMINISTIC,
            "multi_goal", cancelled, EventType.RUN_CANCELLED,
            ("workflow_a_completed", "workflow_b_active", "run_cancelled"),
            ("workflow_a_reexecution", "workflow_c_started", "run_completed"),
            "Completed work is preserved and pending work is never activated after cancel.",
        ),
        D4Case(
            "D405", "submit the same cancel request twice", ProviderMode.DETERMINISTIC,
            "duplicate_cancel", cancelled, EventType.RUN_CANCELLED,
            ("one_cancel_intent", "one_cancelling_transition", "one_terminal_event"),
            ("second_intent", "duplicate_terminal_event", "run_completed"),
            "Retrying the same request is an idempotent control-plane operation.",
        ),
        D4Case(
            "D406", "disconnect after cancel and replay on reconnect", ProviderMode.DETERMINISTIC,
            "client_disconnect", cancelled, EventType.RUN_CANCELLED,
            ("durable_cancel_intent", "event_cursor_replay", "run_cancelled"),
            ("client_owned_execution", "event_gap", "run_completed"),
            "The event consumer is not the owner of cancellation convergence.",
        ),
        D4Case(
            "D407", "restart Service while cancellation is pending", ProviderMode.DETERMINISTIC,
            "service_restart", cancelled, EventType.RUN_CANCELLED,
            ("cancelling_after_restart", "new_worker_observation", "run_cancelled"),
            ("normal_resume", "cancel_intent_loss", "run_completed"),
            "A new process continues cancellation and never resumes normal work.",
        ),
        D4Case(
            "D408", "trigger a real Run deadline while Provider is active", ProviderMode.REAL_PROVIDER,
            "run_deadline", RunStatus.TIMED_OUT, EventType.RUN_TIMED_OUT,
            ("run_timeout_intent", "safe_boundary", "run_timed_out"),
            ("run_completed", "run_cancelled", "next_task_started"),
            "A Run deadline is distinct from a user cancellation and Provider failure.",
        ),
        D4Case(
            "D409", "contrast a Provider timeout with a Run timeout", ProviderMode.DETERMINISTIC,
            "provider_timeout", RunStatus.FAILED_TERMINAL, EventType.RUN_FAILED,
            ("provider_timeout_evidence", "failure_policy", "run_failed"),
            ("run_timed_out", "run_completed", "provider_fallback_from_cancel"),
            "Provider timeout classification remains owned by the failure policy.",
        ),
        D4Case(
            "D410", "cancel an already completed Run", ProviderMode.DETERMINISTIC,
            "completed_run", RunStatus.COMPLETED, EventType.RUN_COMPLETED,
            ("completed_snapshot", "original_terminal_event"),
            ("run_cancelling", "run_cancelled", "new_side_effect"),
            "A terminal completed Run cannot be reopened by a cancel request.",
        ),
    )


def d4_manifest_hash(cases: Iterable[D4Case] | None = None) -> str:
    values = tuple(build_d4_cases() if cases is None else cases)
    payload = [case.to_dict() for case in sorted(values, key=lambda item: item.case_id)]
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_d4_manifest(cases: Iterable[D4Case] | None = None) -> tuple[D4Case, ...]:
    values = tuple(build_d4_cases() if cases is None else cases)
    ids = tuple(case.case_id for case in values)
    expected = tuple(f"D4{index:02d}" for index in range(1, 11))
    if ids != expected:
        raise ValueError(f"D4 case ids must be {expected}, got {ids}")
    if len({d4_manifest_hash((case,)) for case in values}) != len(values):
        raise ValueError("D4 cases must have distinct canonical definitions")
    for case in values:
        if case.expected_terminal_event.is_terminal is not True:
            raise ValueError(f"{case.case_id}: terminal event is required")
        if case.expected_terminal_status in {RunStatus.CANCELLING, RunStatus.RUNNING}:
            raise ValueError(f"{case.case_id}: expected status must be terminal")
    return values


__all__ = [
    "D4Case",
    "MANIFEST_NAME",
    "MANIFEST_VERSION",
    "ProviderMode",
    "build_d4_cases",
    "d4_manifest_hash",
    "validate_d4_manifest",
]
