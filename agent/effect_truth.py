"""Deterministic world-effect truth for Runtime completion decisions.

The LLM may describe an intended action, but it cannot establish that an
external side effect happened.  This module keeps that distinction as plain
data so Planner, Executor, Finalizer, and the Service boundary share one
contract instead of each interpreting prose independently.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Any, Callable, Mapping


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

    @property
    def can_complete(self) -> bool:
        return not self.unresolved_required_effects

    def to_dict(self) -> dict[str, Any]:
        return {
            "required_effects": [dict(item) for item in self.required_effects],
            "verified_effects": [dict(item) for item in self.verified_effects],
            "unsupported_effects": [dict(item) for item in self.unsupported_effects],
            "failed_effects": [dict(item) for item in self.failed_effects],
            "unresolved_required_effects": [
                dict(item) for item in self.unresolved_required_effects
            ],
            "can_complete": self.can_complete,
        }


_RESERVATION_RE = re.compile(
    r"(?:帮我|请(?:帮我)?|我要|我想|帮忙).{0,20}"
    r"(?:订|预订|预定|购买|买|下单|出票).{0,28}"
    r"(?:机票|火车票|高铁票|车票|酒店|房间|餐厅|reservation|booking)",
    re.IGNORECASE,
)
_MESSAGE_SEND_RE = re.compile(
    r"(?:帮我|请(?:帮我)?|我要|我想|帮忙)?.{0,20}"
    r"(?:发送|发出|发)\s*(?:一封)?(?:邮件|短信|消息)|"
    r"send[_ -]?(?:email|message|sms)",
    re.IGNORECASE,
)
_DEPLOY_RE = re.compile(
    r"(?:帮我|请(?:帮我)?|我要|我想|帮忙).{0,12}"
    r"(?:部署|上线|发布到云|发布服务|deploy)",
    re.IGNORECASE,
)
_TRANSACTION_RE = re.compile(
    r"(?:帮我|请(?:帮我)?|我要|我想|帮忙).{0,12}"
    r"(?:下单|付款|支付|转账|购买|买入)",
    re.IGNORECASE,
)
_REMOTE_MUTATION_RE = re.compile(
    r"(?:帮我|请(?:帮我)?|我要|我想|帮忙).{0,20}"
    r"(?:创建|修改|删除).{0,16}(?:github|云端|远程|issue|配置)",
    re.IGNORECASE,
)


def _requirement(
    effect_id: str,
    capability: str,
    target: str,
    description: str,
) -> EffectRequirement:
    return EffectRequirement(
        effect_id=effect_id,
        effect_class=EffectClass.EXTERNAL_EFFECT,
        capability=capability,
        target=target,
        description=description,
    )


def detect_requested_effects(text: str) -> tuple[EffectRequirement, ...]:
    """Extract explicit external mutations without asking an LLM.

    Informational questions such as "如何预订机票" intentionally do not
    match.  The first version only recognizes imperative/action phrasing; it
    can be extended with new effect classes without changing the gate.
    """

    value = str(text or "").strip()
    requirements: list[EffectRequirement] = []
    if _RESERVATION_RE.search(value):
        requirements.append(
            _requirement(
                "external:reservation",
                "reservation",
                "reservation",
                "预订机票、酒店或其他外部资源",
            )
        )
    if _MESSAGE_SEND_RE.search(value):
        requirements.append(
            _requirement(
                "external:message_send",
                "message_send",
                "message",
                "发送邮件、短信或外部消息",
            )
        )
    if _DEPLOY_RE.search(value):
        requirements.append(
            _requirement(
                "external:deployment",
                "deployment",
                "deployment",
                "部署或发布远程服务",
            )
        )
    if _TRANSACTION_RE.search(value):
        requirements.append(
            _requirement(
                "external:transaction",
                "external_transaction",
                "transaction",
                "付款、转账、下单或购买",
            )
        )
    if _REMOTE_MUTATION_RE.search(value):
        requirements.append(
            _requirement(
                "external:remote_mutation",
                "remote_resource_mutation",
                "remote_resource",
                "修改远程资源或云端配置",
            )
        )
    return tuple(requirements)


def infer_effect_class(text: str) -> EffectClass:
    """Classify a request for diagnostics even when no effect is required."""

    if detect_requested_effects(text):
        return EffectClass.EXTERNAL_EFFECT
    from agent.cognition.execution_need import analyze_execution_need

    if analyze_execution_need(text) is True:
        return EffectClass.LOCAL_EFFECT
    return EffectClass.READ_ONLY


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


def execution_truth(state: Mapping[str, Any]) -> ExecutionTruth:
    """Build the deterministic truth projection from Runtime evidence."""

    required = tuple(_copy_records(state.get("required_effects")))
    verified = tuple(_copy_records(state.get("verified_effects")))
    unsupported = tuple(_copy_records(state.get("unsupported_effects")))
    failed = tuple(_copy_records(state.get("failed_effects")))
    verified_ids = {
        str(item.get("effect_id", ""))
        for item in verified
        if str(item.get("status", "VERIFIED")).upper() == "VERIFIED"
    }
    unresolved = tuple(
        item for item in required
        if str(item.get("effect_id", "")) not in verified_ids
    )
    return ExecutionTruth(
        required_effects=required,
        verified_effects=verified,
        unsupported_effects=unsupported,
        failed_effects=failed,
        unresolved_required_effects=unresolved,
    )


def initialize_effect_contract(
    state: Any,
    user_input: str,
    *,
    capability_resolver: Callable[[str, str], str | None] | None = None,
) -> ExecutionTruth:
    """Register requested effects and mark missing capabilities deterministically."""

    state["effect_class"] = infer_effect_class(user_input).value
    detected = detect_requested_effects(user_input)
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
    state["effect_truth_ok"] = truth.can_complete
    if truth.unresolved_required_effects:
        if truth.unsupported_effects:
            state["runtime_failure_code"] = "UNSUPPORTED_CAPABILITY"
            state["runtime_terminal_status"] = "BLOCKED"
        else:
            state["runtime_failure_code"] = "UNVERIFIED_EFFECT"
            state["runtime_terminal_status"] = "FAILED_TERMINAL"
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
    if bool(getattr(result, "success", False)) and executor == "tool" and verifier:
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


def effect_label(requirement: Mapping[str, Any]) -> str:
    labels = {
        "reservation": "预订",
        "message_send": "发送消息",
        "deployment": "部署",
        "external_transaction": "外部交易",
        "remote_resource_mutation": "远程资源修改",
    }
    return labels.get(str(requirement.get("capability", "")), "外部操作")


def has_success_claim(answer: str) -> bool:
    """Detect affirmative external-effect claims, not refusals or explanations."""

    return bool(re.search(
        r"(?:已|已经|成功|完成|订好了|已出票|已发送|已部署|已下单)"
        r".{0,12}(?:预订|预定|出票|发送|发出|部署|上线|下单|付款|支付|转账)",
        str(answer or ""),
        re.IGNORECASE,
    ))


__all__ = [
    "EffectClass",
    "EffectEvidence",
    "EffectRequirement",
    "ExecutionTruth",
    "detect_requested_effects",
    "effect_label",
    "enforce_completion_gate",
    "execution_truth",
    "has_success_claim",
    "infer_effect_class",
    "initialize_effect_contract",
    "record_effect_result",
]
