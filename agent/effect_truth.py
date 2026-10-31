"""Deterministic world-effect truth for Runtime completion decisions.

The LLM may describe an intended action, but it cannot establish that an
external side effect happened.  This module keeps that distinction as plain
data so Planner, Executor, Finalizer, and the Service boundary share one
contract instead of each interpreting prose independently.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Mapping, Sequence

from agent.cognition.execution_need import RequestedOutcome


class EffectClass(str, Enum):
    """Risk class of the world state requested by the user."""

    READ_ONLY = "READ_ONLY"
    LOCAL_EFFECT = "LOCAL_EFFECT"
    EXTERNAL_EFFECT = "EXTERNAL_EFFECT"


@dataclass(frozen=True)
class EffectRequirement:
    """A side effect that must be evidenced before a Run may complete."""

    effect_id: str
    effect_class: EffectClass
    capability: str
    target: str
    description: str

    def to_dict(self) -> dict[str, str]:
        return {
            "effect_id": self.effect_id,
            "effect_class": self.effect_class.value,
            "capability": self.capability,
            "target": self.target,
            "description": self.description,
        }


@dataclass(frozen=True)
class EffectEvidence:
    """Verifier-produced evidence for one required effect."""

    effect_id: str
    status: str
    source: str
    detail: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "effect_id": self.effect_id,
            "status": self.status,
            "source": self.source,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class ExecutionTruth:
    """Read-only projection used by the completion gate."""

    required_effects: tuple[dict[str, Any], ...]
    verified_effects: tuple[dict[str, Any], ...]
    unsupported_effects: tuple[dict[str, Any], ...]
    failed_effects: tuple[dict[str, Any], ...]
    unresolved_required_effects: tuple[dict[str, Any], ...]
    requested_outcomes: tuple[str, ...]
    execution_evidence: tuple[dict[str, Any], ...]
    unresolved_requested_outcomes: tuple[str, ...]

    @property
    def can_complete(self) -> bool:
        return not (
            self.unresolved_required_effects
            or self.unresolved_requested_outcomes
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "required_effects": [dict(item) for item in self.required_effects],
            "verified_effects": [dict(item) for item in self.verified_effects],
            "unsupported_effects": [dict(item) for item in self.unsupported_effects],
            "failed_effects": [dict(item) for item in self.failed_effects],
            "unresolved_required_effects": [
                dict(item) for item in self.unresolved_required_effects
            ],
            "requested_outcomes": list(self.requested_outcomes),
            "execution_evidence": [
                dict(item) for item in self.execution_evidence
            ],
            "unresolved_requested_outcomes": list(
                self.unresolved_requested_outcomes
            ),
            "can_complete": self.can_complete,
        }


def _copy_records(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        return []
    records: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, Mapping):
            records.append(dict(item))
        elif isinstance(item, str) and item.strip():
            records.append({"effect_id": item.strip()})
    return records


_EVIDENCE_REQUIRED_OUTCOMES = frozenset({
    RequestedOutcome.FILE_READ.value,
    RequestedOutcome.FILE_MUTATION.value,
    RequestedOutcome.CODE_EXECUTION.value,
    RequestedOutcome.COMMAND_EXECUTION.value,
})


def _copy_outcomes(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    outcomes: list[str] = []
    for item in value:
        normalized = str(getattr(item, "value", item) or "").strip()
        if normalized and normalized not in outcomes:
            outcomes.append(normalized)
    return tuple(outcomes)


def execution_truth(state: Mapping[str, Any]) -> ExecutionTruth:
    """Build the deterministic truth projection from Runtime evidence."""

    required = tuple(_copy_records(state.get("required_effects")))
    verified = tuple(_copy_records(state.get("verified_effects")))
    unsupported = tuple(_copy_records(state.get("unsupported_effects")))
    failed = tuple(_copy_records(state.get("failed_effects")))
    requested_outcomes = _copy_outcomes(state.get("requested_outcomes"))
    execution_evidence = tuple(_copy_records(state.get("execution_evidence")))
    verified_ids = {
        str(item.get("effect_id", ""))
        for item in verified
        if str(item.get("status", "VERIFIED")).upper() == "VERIFIED"
    }
    unresolved = tuple(
        item for item in required
        if str(item.get("effect_id", "")) not in verified_ids
    )
    verified_outcomes = {
        str(item.get("outcome", ""))
        for item in execution_evidence
        if str(item.get("status", "VERIFIED")).upper() == "VERIFIED"
    }
    unresolved_outcomes = tuple(
        outcome
        for outcome in requested_outcomes
        if outcome in _EVIDENCE_REQUIRED_OUTCOMES
        and outcome not in verified_outcomes
    )
    return ExecutionTruth(
        required_effects=required,
        verified_effects=verified,
        unsupported_effects=unsupported,
        failed_effects=failed,
        unresolved_required_effects=unresolved,
        requested_outcomes=requested_outcomes,
        execution_evidence=execution_evidence,
        unresolved_requested_outcomes=unresolved_outcomes,
    )


_EXTERNAL_EFFECT_REQUIREMENTS = {
    "reservation": (
        "external:reservation",
        "reservation",
        "reservation",
        "预订机票、酒店或其他外部资源",
    ),
    "message_send": (
        "external:message_send",
        "message_send",
        "message",
        "发送邮件、短信或外部消息",
    ),
    "deployment": (
        "external:deployment",
        "deployment",
        "deployment",
        "部署或发布远程服务",
    ),
    "transaction": (
        "external:transaction",
        "external_transaction",
        "transaction",
        "付款、转账、下单或购买",
    ),
    "remote_mutation": (
        "external:remote_mutation",
        "remote_resource_mutation",
        "remote_resource",
        "修改远程资源或云端配置",
    ),
}


def initialize_effect_contract(
    state: Any,
    user_input: str,
    *,
    external_effects: Sequence[Mapping[str, str]] = (),
    capability_resolver: Callable[[str, str], str | None] | None = None,
) -> ExecutionTruth:
    """Register intent-declared effects and mark missing capabilities."""

    state["requested_outcomes"] = list(dict.fromkeys(
        state.get("requested_outcomes", [])
    ))
    detected = tuple(
        EffectRequirement(
            effect_id=spec[0],
            effect_class=EffectClass.EXTERNAL_EFFECT,
            capability=spec[1],
            target=spec[2],
            description=spec[3],
        )
        for capability in (
            str(item.get("capability", "")) for item in external_effects
        )
        for spec in (_EXTERNAL_EFFECT_REQUIREMENTS.get(capability),)
        if spec is not None
    )
    state["effect_class"] = (
        EffectClass.EXTERNAL_EFFECT.value
        if detected
        else (
            EffectClass.LOCAL_EFFECT.value
            if state["requested_outcomes"]
            else EffectClass.READ_ONLY.value
        )
    )
    existing = _copy_records(state.get("required_effects"))
    existing_ids = {str(item.get("effect_id", "")) for item in existing}
    for detected_requirement in detected:
        if detected_requirement.effect_id not in existing_ids:
            existing.append(detected_requirement.to_dict())
    state["required_effects"] = existing

    if capability_resolver is not None:
        unsupported = _copy_records(state.get("unsupported_effects"))
        unsupported_ids = {str(item.get("effect_id", "")) for item in unsupported}
        verified_ids = {
            str(item.get("effect_id", ""))
            for item in _copy_records(state.get("verified_effects"))
            if str(item.get("status", "VERIFIED")).upper() == "VERIFIED"
        }
        for required_record in existing:
            effect_id = str(required_record.get("effect_id", ""))
            capability = str(required_record.get("capability", ""))
            # A previously committed and verified effect remains valid during
            # resume even if the current process no longer exposes the
            # capability that originally produced it.
            if not effect_id or effect_id in unsupported_ids or effect_id in verified_ids:
                continue
            if capability_resolver(capability, user_input) is None:
                unsupported.append({
                    **required_record,
                    "status": "UNSUPPORTED",
                    "reason_code": "UNSUPPORTED_CAPABILITY",
                })
                unsupported_ids.add(effect_id)
        state["unsupported_effects"] = unsupported
        if unsupported:
            state["runtime_failure_code"] = "UNSUPPORTED_CAPABILITY"
            state["runtime_terminal_status"] = "BLOCKED"

    truth = execution_truth(state)
    state["unresolved_required_effects"] = [
        dict(item) for item in truth.unresolved_required_effects
    ]
    state["effect_truth_ok"] = truth.can_complete
    return truth


def enforce_completion_gate(state: Any) -> ExecutionTruth:
    """Prevent COMPLETED when any required effect lacks verified evidence."""

    truth = execution_truth(state)
    state["unresolved_required_effects"] = [
        dict(item) for item in truth.unresolved_required_effects
    ]
    state["unresolved_requested_outcomes"] = list(
        truth.unresolved_requested_outcomes
    )
    state["effect_truth_ok"] = truth.can_complete
    if truth.unresolved_required_effects or truth.unresolved_requested_outcomes:
        if truth.unsupported_effects:
            state["runtime_failure_code"] = "UNSUPPORTED_CAPABILITY"
            state["runtime_terminal_status"] = "BLOCKED"
        else:
            if not state.get("runtime_failure_code"):
                task_failure = next((
                    str(task["error_code"]) for task in state.get("plan", []) or []
                    if task.get("status") == "failed" and task.get("error_code")
                ), "")
                state["runtime_failure_code"] = task_failure or (
                    "EXECUTION_EVIDENCE_MISSING" if truth.unresolved_requested_outcomes else "UNVERIFIED_EFFECT"
                )
            if state.get("runtime_terminal_status") not in {"BLOCKED", "CANCELLED", "TIMED_OUT"}:
                state["runtime_terminal_status"] = (
                    "BLOCKED" if state["runtime_failure_code"] == "EXECUTION_ENVIRONMENT_UNAVAILABLE"
                    else "FAILED_TERMINAL"
                )
    return truth


def record_effect_result(
    state: Any,
    task: Mapping[str, Any],
    plan: Any,
    result: Any,
) -> None:
    """Record only verifier-backed task effects; LLM prose is never evidence."""

    inputs = task.get("inputs") or {}
    if not isinstance(inputs, Mapping):
        return
    effect_id = str(inputs.get("effect_id", "") or "").strip()
    if not effect_id:
        return
    metadata = getattr(result, "metadata", None) or {}
    executor = str(getattr(plan, "executor", "") or "")
    verifier = str(metadata.get("verifier", "") or "")
    if (
        bool(getattr(result, "success", False))
        and executor == "tool"
        and verifier not in {"", "none"}
    ):
        evidence = EffectEvidence(
            effect_id=effect_id,
            status="VERIFIED",
            source=f"ExecutionVerifier:{verifier}",
        ).to_dict()
        values = _copy_records(state.get("verified_effects"))
        if effect_id not in {str(item.get("effect_id", "")) for item in values}:
            values.append(evidence)
        state["verified_effects"] = values
        return
    if not bool(getattr(result, "success", False)):
        values = _copy_records(state.get("failed_effects"))
        values.append(EffectEvidence(
            effect_id=effect_id,
            status="FAILED",
            source=executor or "executor",
            detail=str(getattr(result, "error", "") or "")[:240],
        ).to_dict())
        state["failed_effects"] = values


_STEP_OUTCOME = {
    "filesystem.read": RequestedOutcome.FILE_READ.value,
    "filesystem.write": RequestedOutcome.FILE_MUTATION.value,
    "filesystem.copy": RequestedOutcome.FILE_MUTATION.value,
    "filesystem.move": RequestedOutcome.FILE_MUTATION.value,
    "filesystem.delete": RequestedOutcome.FILE_MUTATION.value,
    "run_python": RequestedOutcome.CODE_EXECUTION.value,
    "run_python_file": RequestedOutcome.CODE_EXECUTION.value,
    "shell": RequestedOutcome.COMMAND_EXECUTION.value,
    "process.execute": RequestedOutcome.COMMAND_EXECUTION.value,
}


def record_execution_evidence(state: Any, plan: Any, result: Any) -> None:
    """Record verified execution facts, never LLM output prose."""

    if not bool(getattr(result, "success", False)):
        return
    if str(getattr(plan, "executor", "") or "") != "tool":
        return
    action_result = getattr(result, "action_result", None)
    if action_result is None or action_result.verified is not True:
        return
    steps = getattr(plan, "steps", ()) or ()
    evidence = _copy_records(state.get("execution_evidence"))
    existing = {
        (str(item.get("outcome", "")), str(item.get("target", "")))
        for item in evidence
    }
    metadata = getattr(result, "metadata", None) or {}
    verifier = str(metadata.get("verifier", "") or "")
    for path, source in (
        *((item["path"], "ExecutionVerifier:file") for item in metadata.get("verified_files", [])),
        *((str(path), "ExecutionVerifier:file_operation") for path in metadata.get("consumed_files", [])),
    ):
        identity = (RequestedOutcome.FILE_MUTATION.value, path)
        if identity in existing:
            continue
        evidence.append({
            "outcome": RequestedOutcome.FILE_MUTATION.value,
            "status": "VERIFIED",
            "source": source,
            "target": path[:240],
        })
        existing.add(identity)
    for step in steps:
        tool = str(getattr(step, "tool", "") or "")
        outcome = _STEP_OUTCOME.get(tool)
        if outcome is None or outcome == RequestedOutcome.FILE_MUTATION.value:
            continue
        args = getattr(step, "args", {}) or {}
        target = str(
            args.get("path")
            or args.get("destination")
            or args.get("cmd")
            or args.get("code")
            or ""
        )[:240]
        identity = (outcome, target)
        if identity in existing:
            continue
        evidence.append({
            "outcome": outcome,
            "status": "VERIFIED",
            "source": (
                f"ExecutionVerifier:{verifier}"
                if verifier not in {"", "none"}
                else "ToolExecutor"
            ),
            "target": target,
        })
        existing.add(identity)
    state["execution_evidence"] = evidence


def effect_label(requirement: Mapping[str, Any]) -> str:
    labels = {
        "reservation": "预订",
        "message_send": "发送消息",
        "deployment": "部署",
        "external_transaction": "外部交易",
        "remote_resource_mutation": "远程资源修改",
    }
    return labels.get(str(requirement.get("capability", "")), "外部操作")


__all__ = [
    "EffectClass",
    "EffectEvidence",
    "EffectRequirement",
    "ExecutionTruth",
    "effect_label",
    "enforce_completion_gate",
    "execution_truth",
    "initialize_effect_contract",
    "record_effect_result",
    "record_execution_evidence",
]
