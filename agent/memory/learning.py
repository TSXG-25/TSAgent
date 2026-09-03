"""The production boundary for deciding whether interaction evidence is learned.

This module is deliberately storage-free.  It turns an already projected
interaction into a small, auditable decision; persistence is owned by
``agent.memory.persistence``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Mapping


VALID_ACTIONS = frozenset({"STORE", "UPDATE", "IGNORE"})
VALID_SCOPES = frozenset({"session", "user", "repository"})
VALID_MEMORY_TYPES = frozenset({"fact", "preference", "summary", "resolution"})
DEFAULT_AUTHORIZED_SOURCE_KINDS = frozenset(
    {"user_statement", "user_confirmed_resolution"}
)
_SECRET_FIELD_RE = re.compile(
    r"api[ _-]?key|access[ _-]?token|refresh[ _-]?token|password|passwd|"
    r"credential|secret|private[ _-]?key|密钥|密码|口令|令牌|私钥",
    re.IGNORECASE,
)
_SECRET_VALUE_RE = re.compile(
    r"(?:^|\b)(?:sk-[A-Za-z0-9_-]+|gh[pousr]_[A-Za-z0-9_]+|AKIA[A-Z0-9]{12,})",
    re.IGNORECASE,
)
_SENSITIVE_FIELD_RE = re.compile(
    r"email|e-mail|phone|mobile|address|身份证|邮箱|手机号|电话|住址|生日",
    re.IGNORECASE,
)
_SENSITIVE_VALUE_RE = re.compile(
    r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}|(?<!\d)1\d{10}(?!\d)",
    re.IGNORECASE,
)
_VOLATILE_FIELD_RE = re.compile(
    r"price|weather|stock|market|news|exchange|rate|today|current|latest|"
    r"价格|天气|股票|股市|新闻|汇率|行情|今天|今日|当前|最新|实时|近期|本周",
    re.IGNORECASE,
)


def _required_text(value: str, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field_name} must be non-empty")
    return normalized


@dataclass(frozen=True, slots=True)
class ExistingMemory:
    """The one same-scope/key value projected to the learning policy."""

    scope: str
    canonical_key: str
    value: str

    def __post_init__(self) -> None:
        if self.scope not in VALID_SCOPES:
            raise ValueError(f"invalid memory scope: {self.scope}")
        _required_text(self.canonical_key, "existing canonical_key")
        _required_text(self.value, "existing value")


@dataclass(frozen=True, slots=True)
class ResolutionEvidence:
    """Minimal canonical payload required to persist a resolution fact."""

    utterance: str
    kind: str
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _required_text(self.utterance, "resolution utterance")
        _required_text(self.kind, "resolution kind")
        if not isinstance(self.metadata, Mapping):
            raise TypeError("resolution metadata must be a mapping")

    def to_dict(self) -> dict[str, object]:
        return {
            "utterance": self.utterance,
            "kind": self.kind,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class InteractionEvidence:
    """Runtime-projected evidence presented to the learning policy."""

    evidence_id: str
    source_kind: str
    source_ref: str
    text: str
    memory_type: str
    requested_scope: str
    canonical_key: str
    value: str
    explicit_persist: bool
    sensitive: bool
    secret: bool
    volatile: bool
    existing: ExistingMemory | None = None
    resolution: ResolutionEvidence | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        for name in ("evidence_id", "source_kind", "source_ref", "text", "memory_type", "requested_scope"):
            _required_text(getattr(self, name), name)
        if self.memory_type not in VALID_MEMORY_TYPES:
            raise ValueError(f"invalid memory type: {self.memory_type}")
        if self.requested_scope not in VALID_SCOPES:
            raise ValueError(f"invalid requested scope: {self.requested_scope}")
        if self.resolution is not None:
            if not isinstance(self.resolution, ResolutionEvidence):
                raise TypeError("resolution must be ResolutionEvidence")
            if self.memory_type != "resolution":
                raise ValueError("resolution evidence is only valid for resolution memory")
        for name in ("explicit_persist", "sensitive", "secret", "volatile"):
            if not isinstance(getattr(self, name), bool):
                raise TypeError(f"{name} must be bool")


@dataclass(frozen=True, slots=True)
class MemoryPolicyProjection:
    """Typed scope and authorization facts supplied by the Runtime."""

    scope: str
    namespace: str
    allow_persist: bool = True
    allowed_memory_types: frozenset[str] = field(
        default_factory=lambda: frozenset(VALID_MEMORY_TYPES)
    )
    authorized_source_kinds: frozenset[str] = field(
        default_factory=lambda: frozenset(DEFAULT_AUTHORIZED_SOURCE_KINDS)
    )

    def __post_init__(self) -> None:
        if self.scope not in VALID_SCOPES:
            raise ValueError(f"invalid policy scope: {self.scope}")
        namespace = _required_text(self.namespace, "memory namespace")
        if namespace in {".", ".."} or "/" in namespace or "\\" in namespace:
            raise ValueError("memory namespace must be path-safe")
        if not isinstance(self.allow_persist, bool):
            raise TypeError("allow_persist must be bool")
        if not self.allowed_memory_types <= VALID_MEMORY_TYPES:
            raise ValueError("policy contains an invalid memory type")


@dataclass(frozen=True, slots=True)
class MemoryLearningDecision:
    """The only decision shape that may reach the persistence boundary."""

    action: str
    memory_type: str
    scope: str
    canonical_key: str
    value: str
    provenance: Mapping[str, str]
    reason_code: str
    # Resolution payload is bound from projected evidence and intentionally
    # excluded from ``to_dict``; the Provider contract remains seven fields.
    resolution: ResolutionEvidence | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        if self.action not in VALID_ACTIONS:
            raise ValueError(f"invalid learning action: {self.action}")
        _required_text(self.reason_code, "reason_code")
        if self.action == "IGNORE":
            if any((self.memory_type, self.scope, self.canonical_key, self.value)):
                raise ValueError("IGNORE cannot carry write fields")
            if self.provenance:
                raise ValueError("IGNORE cannot carry provenance")
            if self.resolution is not None:
                raise ValueError("IGNORE cannot carry resolution evidence")
            return
        if self.memory_type not in VALID_MEMORY_TYPES:
            raise ValueError(f"invalid decision memory type: {self.memory_type}")
        if self.scope not in VALID_SCOPES:
            raise ValueError(f"invalid decision scope: {self.scope}")
        _required_text(self.canonical_key, "canonical_key")
        _required_text(self.value, "value")
        required_provenance = {"evidence_id", "source_kind", "source_ref"}
        if set(self.provenance) != required_provenance:
            raise ValueError("write decision provenance is incomplete")
        for key in required_provenance:
            _required_text(self.provenance[key], f"provenance.{key}")
        if self.resolution is not None:
            if not isinstance(self.resolution, ResolutionEvidence):
                raise TypeError("resolution must be ResolutionEvidence")
            if self.memory_type != "resolution":
                raise ValueError("resolution evidence is only valid for resolution memory")

    @classmethod
    def ignore(cls, reason_code: str) -> "MemoryLearningDecision":
        return cls(
            action="IGNORE",
            memory_type="",
            scope="",
            canonical_key="",
            value="",
            provenance={},
            reason_code=reason_code,
        )

    def to_dict(self) -> dict[str, object]:
        """Serialize only the frozen contract fields."""
        return {
            "action": self.action,
            "memory_type": self.memory_type,
            "scope": self.scope,
            "canonical_key": self.canonical_key,
            "value": self.value,
            "provenance": dict(self.provenance),
            "reason_code": self.reason_code,
        }


# This name is retained as the policy owner expected by the D-1 discovery
# contract.  It is an alias, not a second policy implementation.
MemoryLearningPolicy = MemoryPolicyProjection


def _source_rejection(evidence: InteractionEvidence) -> str | None:
    if evidence.source_kind == "user_request":
        if re.search(r"忘记|删除|清除|不要记", evidence.text):
            return "DELETE_OWNED_BY_LIFECYCLE"
        return "NO_PERSISTABLE_EVIDENCE"
    if evidence.source_kind == "assistant_output":
        if evidence.existing is not None:
            return "CONFLICT_UNCONFIRMED"
        return (
            "ASSISTANT_OUTPUT_NOT_EVIDENCE"
            if evidence.memory_type == "summary"
            else "ASSISTANT_INFERENCE"
        )
    if evidence.source_kind == "repository_observation":
        return "REPOSITORY_INDEX_OUT_OF_SCOPE"
    if evidence.source_kind == "run_artifact":
        return "RUN_FACT_NOT_USER_MEMORY"
    return None


def _inferred_safety_flags(evidence: InteractionEvidence) -> tuple[bool, bool, bool]:
    """Recheck safety-sensitive fields at the policy boundary.

    The producer normally projects these flags.  Rechecking canonical field
    names and obvious secret values here prevents a new writer that forgets a
    flag from bypassing the durable-memory safety contract.
    """
    field = evidence.canonical_key
    generic_field = field.rsplit(".", 1)[-1].lower() in {
        "fact",
        "preference",
        "value",
    }
    secret = bool(
        evidence.secret
        or _SECRET_FIELD_RE.search(field)
        or _SECRET_VALUE_RE.search(evidence.value)
    )
    sensitive = bool(
        evidence.sensitive
        or _SENSITIVE_FIELD_RE.search(field)
        or _SENSITIVE_VALUE_RE.search(evidence.value)
        or (generic_field and _SENSITIVE_FIELD_RE.search(evidence.text))
    )
    volatile = bool(evidence.volatile or _VOLATILE_FIELD_RE.search(field))
    return secret, sensitive, volatile


def decide_memory_learning(
    evidence: InteractionEvidence,
    policy: MemoryPolicyProjection,
) -> MemoryLearningDecision:
    """Apply the deterministic authorization and deduplication policy."""

    secret, sensitive, volatile = _inferred_safety_flags(evidence)
    if secret:
        return MemoryLearningDecision.ignore("SECRET_NEVER_STORE")
    if volatile:
        return MemoryLearningDecision.ignore("VOLATILE_OBSERVATION")

    source_rejection = _source_rejection(evidence)
    if source_rejection is not None:
        return MemoryLearningDecision.ignore(source_rejection)
    if evidence.requested_scope != policy.scope:
        return MemoryLearningDecision.ignore("SCOPE_WIDENING_DENIED")
    if evidence.memory_type not in policy.allowed_memory_types:
        return MemoryLearningDecision.ignore("MEMORY_TYPE_NOT_ALLOWED")
    if evidence.source_kind not in policy.authorized_source_kinds:
        return MemoryLearningDecision.ignore("SOURCE_NOT_AUTHORIZED")
    if not policy.allow_persist:
        return MemoryLearningDecision.ignore("PERSISTENCE_NOT_AUTHORIZED")
    if sensitive and not evidence.explicit_persist:
        return MemoryLearningDecision.ignore(
            "SENSITIVE_WITHOUT_EXPLICIT_PERSISTENCE"
        )
    if not evidence.explicit_persist:
        return MemoryLearningDecision.ignore("NO_PERSISTABLE_EVIDENCE")
    if not evidence.canonical_key.strip() or not evidence.value.strip():
        return MemoryLearningDecision.ignore("INVALID_EVIDENCE")

    if evidence.existing is not None:
        if (
            evidence.existing.scope != policy.scope
            or evidence.existing.canonical_key != evidence.canonical_key
        ):
            return MemoryLearningDecision.ignore("SCOPE_WIDENING_DENIED")
        if evidence.existing.value == evidence.value:
            return MemoryLearningDecision.ignore("DUPLICATE_SAME_VALUE")
        action = "UPDATE"
        reason_code = "EXPLICIT_VALUE_UPDATE"
    else:
        action = "STORE"
        if evidence.memory_type == "resolution":
            reason_code = "CONFIRMED_RESOLUTION"
        elif policy.scope == "repository" and evidence.memory_type == "preference":
            reason_code = "EXPLICIT_PROJECT_PREFERENCE"
        elif policy.scope == "repository":
            reason_code = "EXPLICIT_REPOSITORY_FACT"
        elif policy.scope == "session":
            reason_code = "EXPLICIT_SESSION_PREFERENCE"
        elif (
            evidence.memory_type == "preference"
            and not re.search(r"记住|以后|默认|优先|偏好|请", evidence.text)
        ):
            reason_code = "NEW_CANONICAL_FACT"
        elif evidence.memory_type == "preference":
            reason_code = "EXPLICIT_USER_PREFERENCE"
        else:
            reason_code = "EXPLICIT_USER_FACT"
        if sensitive:
            reason_code = "EXPLICIT_SENSITIVE_PERSISTENCE"

    return MemoryLearningDecision(
        action=action,
        memory_type=evidence.memory_type,
        scope=policy.scope,
        canonical_key=evidence.canonical_key.strip(),
        value=evidence.value.strip(),
        provenance={
            "evidence_id": evidence.evidence_id,
            "source_kind": evidence.source_kind,
            "source_ref": evidence.source_ref,
        },
        reason_code=reason_code,
        resolution=(
            evidence.resolution
            if evidence.memory_type == "resolution"
            else None
        ),
    )


def authorize_memory_learning_proposal(
    evidence: InteractionEvidence,
    policy: MemoryPolicyProjection,
    proposal: MemoryLearningDecision,
) -> MemoryLearningDecision:
    """Authorize a Provider proposal without letting it bypass D-2 policy.

    The Provider is only a decision source.  This handoff reuses the existing
    deterministic policy as the authority for safety, scope, provenance,
    deduplication, and update eligibility.  A Provider may conservatively
    propose ``IGNORE`` for otherwise eligible evidence, but it can never turn
    policy-approved evidence into an unauthorized write.
    """

    authorized = decide_memory_learning(evidence, policy)
    if authorized.action == "IGNORE":
        # Policy vetoes always win, including secret, volatile, sensitive,
        # source, and scope violations.
        return authorized
    if proposal.action == "IGNORE":
        return MemoryLearningDecision.ignore("PROVIDER_PROPOSED_IGNORE")

    expected = authorized.to_dict()
    candidate = proposal.to_dict()
    comparable_fields = (
        "action",
        "memory_type",
        "scope",
        "canonical_key",
        "value",
        "provenance",
    )
    if any(candidate[field] != expected[field] for field in comparable_fields):
        return MemoryLearningDecision.ignore("PROVIDER_PROPOSAL_REJECTED")
    return authorized


__all__ = [
    "DEFAULT_AUTHORIZED_SOURCE_KINDS",
    "ExistingMemory",
    "InteractionEvidence",
    "MemoryLearningDecision",
    "MemoryLearningPolicy",
    "MemoryPolicyProjection",
    "ResolutionEvidence",
    "VALID_ACTIONS",
    "VALID_MEMORY_TYPES",
    "VALID_SCOPES",
    "authorize_memory_learning_proposal",
    "decide_memory_learning",
]
