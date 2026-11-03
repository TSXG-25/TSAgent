"""Provider-backed Memory Learning decision boundary.

The Provider proposes one canonical ``MemoryLearningDecision`` from projected
evidence, existing-memory facts, and policy facts.  It never reads a store,
commits a write, mutates Runtime state, or owns authorization.  Callers must
pass the proposal through ``authorize_memory_learning_proposal`` before it can
reach ``MemoryPersistenceBoundary``.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
from typing import Any, Literal, Mapping

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent.execution_errors import stable_error_message
from agent.interruption import RunInterruptionRequested, await_interruptibly
from agent.memory.learning import (
    ExistingMemory,
    InteractionEvidence,
    MemoryLearningDecision,
    MemoryPolicyProjection,
)


class MemoryLearningSelectionError(RuntimeError):
    """Stable Provider/schema failure for one learning decision proposal."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        provider: str,
        format_path: str,
        raw_output: dict[str, Any] | str | None = None,
        candidate: MemoryLearningDecision | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.provider = provider
        self.format_path = format_path
        self.raw_output = raw_output
        self.candidate = candidate


@dataclass(frozen=True)
class MemoryLearningSelectionEvidence:
    """Provider/format path for one bounded proposal."""

    provider: str
    provider_path: Literal["SINGLE_PROVIDER"]
    format_path: Literal[
        "STRUCTURED_ONLY", "STRUCTURED_TO_RAW_FALLBACK", "RAW_ONLY",
    ]
    raw_output: dict[str, Any] | str
    structured_error: str = ""


@dataclass(frozen=True)
class MemoryLearningSelection:
    """One uncommitted Provider proposal and its transport evidence."""

    decision: MemoryLearningDecision
    evidence: MemoryLearningSelectionEvidence


class _MemoryLearningDecisionSchema(BaseModel):
    """Private transport schema with the public decision's exact fields."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["STORE", "UPDATE", "IGNORE"]
    memory_type: str = ""
    scope: str = ""
    canonical_key: str = ""
    value: str = ""
    provenance: dict[str, str] = Field(default_factory=dict)
    reason_code: str

    @model_validator(mode="after")
    def _validate_action_fields(self) -> "_MemoryLearningDecisionSchema":
        if self.action == "IGNORE":
            if any((
                self.memory_type,
                self.scope,
                self.canonical_key,
                self.value,
                self.provenance,
            )):
                raise ValueError("IGNORE cannot carry write fields")
            return self
        if not all((
            self.memory_type,
            self.scope,
            self.canonical_key,
            self.value,
        )):
            raise ValueError("write decision fields must be non-empty")
        if set(self.provenance) != {"evidence_id", "source_kind", "source_ref"}:
            raise ValueError("write decision provenance is incomplete")
        return self


MEMORY_LEARNING_PROMPT = """You are TSAgent's bounded Memory Learning Provider.

Propose exactly one canonical MemoryLearningDecision from the projected
InteractionEvidence, ExistingMemory projection, and policy projection. Return
one JSON object with exactly these seven fields:
action, memory_type, scope, canonical_key, value, provenance, reason_code.

The allowed actions are STORE, UPDATE, and IGNORE.

For STORE or UPDATE, copy memory_type, scope, canonical_key, and value from the
projected evidence. Copy provenance exactly from the evidence using the keys
evidence_id, source_kind, and source_ref. Do not invent or widen any field.
For IGNORE, memory_type, scope, canonical_key, value, and provenance must all
be empty, and reason_code must explain why no write is proposed.

Use existing memory only to distinguish a new value, an explicit update, or a
duplicate. Treat policy facts as authoritative. A Provider proposal is not a
commit: never claim that Memory was written, never access a Store, and never
choose retry, fallback, scope widening, or persistence behavior.
"""


class MemoryLearningProvider:
    """Production entry for one Provider-backed learning proposal."""

    def __init__(
        self,
        *,
        provider: Any | None = None,
        provider_name: str = "",
        supports_structured_output: bool | None = None,
    ) -> None:
        self._provider = provider
        self._provider_name = provider_name
        self._supports_structured_output = supports_structured_output

    async def select(
        self,
        evidence: InteractionEvidence,
        existing_memory: ExistingMemory | None,
        policy: MemoryPolicyProjection,
    ) -> MemoryLearningDecision:
        """Return one uncommitted canonical Provider proposal."""

        selection = await self.select_with_evidence(
            evidence,
            existing_memory,
            policy,
        )
        return selection.decision

    async def select_with_evidence(
        self,
        evidence: InteractionEvidence,
        existing_memory: ExistingMemory | None,
        policy: MemoryPolicyProjection,
    ) -> MemoryLearningSelection:
        bound_evidence = self._bind_existing_memory(evidence, existing_memory)
        provider, provider_name, supports_structured = self._resolve_provider()
        messages = self._messages(bound_evidence, policy)
        structured_error = ""
        format_path: Literal[
            "STRUCTURED_ONLY", "STRUCTURED_TO_RAW_FALLBACK", "RAW_ONLY",
        ]

        if supports_structured:
            try:
                structured = provider.with_structured_output(
                    _MemoryLearningDecisionSchema
                )
                response = await await_interruptibly(structured.ainvoke(messages))
                schema = self._schema_from_structured(response)
                raw_output: dict[str, Any] | str = schema.model_dump()
                format_path = "STRUCTURED_ONLY"
            except RunInterruptionRequested:
                raise
            except Exception as error:
                structured_error = stable_error_message(
                    error,
                    fallback="structured Memory Learning proposal failed",
                )
                format_path = "STRUCTURED_TO_RAW_FALLBACK"
                schema, raw_output = await self._select_raw(
                    provider,
                    provider_name,
                    messages,
                    format_path,
                )
        else:
            format_path = "RAW_ONLY"
            schema, raw_output = await self._select_raw(
                provider,
                provider_name,
                messages,
                format_path,
            )

        try:
            decision = MemoryLearningDecision(
                action=schema.action,
                memory_type=schema.memory_type,
                scope=schema.scope,
                canonical_key=schema.canonical_key,
                value=schema.value,
                provenance=dict(schema.provenance),
                reason_code=schema.reason_code,
                resolution=(
                    bound_evidence.resolution
                    if (
                        schema.action != "IGNORE"
                        and bound_evidence.memory_type == "resolution"
                    )
                    else None
                ),
            )
        except Exception as error:
            raise MemoryLearningSelectionError(
                "SCHEMA_INVALID",
                stable_error_message(
                    error,
                    fallback="Memory Learning proposal is not canonical",
                ),
                provider=provider_name,
                format_path=format_path,
                raw_output=raw_output,
            ) from error

        return MemoryLearningSelection(
            decision=decision,
            evidence=MemoryLearningSelectionEvidence(
                provider=provider_name,
                provider_path="SINGLE_PROVIDER",
                format_path=format_path,
                raw_output=raw_output,
                structured_error=structured_error,
            ),
        )

    async def _select_raw(
        self,
        provider: Any,
        provider_name: str,
        messages: list[Any],
        format_path: str,
    ) -> tuple[_MemoryLearningDecisionSchema, str]:
        try:
            response = await await_interruptibly(provider.ainvoke(messages))
        except RunInterruptionRequested:
            raise
        except Exception as error:
            raise MemoryLearningSelectionError(
                "PROVIDER_ERROR",
                stable_error_message(
                    error,
                    fallback="Memory Learning Provider request failed",
                ),
                provider=provider_name,
                format_path=format_path,
            ) from error
        raw_output = str(getattr(response, "content", ""))
        try:
            return (
                _MemoryLearningDecisionSchema.model_validate_json(raw_output),
                raw_output,
            )
        except Exception as error:
            raise MemoryLearningSelectionError(
                "SCHEMA_INVALID",
                stable_error_message(
                    error,
                    fallback="Memory Learning Provider response is not canonical JSON",
                ),
                provider=provider_name,
                format_path=format_path,
                raw_output=raw_output,
            ) from error

    def _resolve_provider(self) -> tuple[Any, str, bool]:
        if self._provider is not None:
            provider_name = self._provider_name or type(self._provider).__name__
            supports_structured = (
                self._supports_structured_output
                if self._supports_structured_output is not None
                else callable(getattr(self._provider, "with_structured_output", None))
            )
            return self._provider, provider_name, supports_structured

        from agent.llm import llm

        provider, provider_name = llm._get_active_provider()
        return provider, provider_name, provider_name != "ollama"

    @staticmethod
    def _bind_existing_memory(
        evidence: InteractionEvidence,
        existing_memory: ExistingMemory | None,
    ) -> InteractionEvidence:
        if evidence.existing is not None and evidence.existing != existing_memory:
            raise ValueError(
                "existing memory projection does not match evidence.existing"
            )
        return replace(evidence, existing=existing_memory)

    @staticmethod
    def _messages(
        evidence: InteractionEvidence,
        policy: MemoryPolicyProjection,
    ) -> list[Any]:
        evidence_payload = {
            "evidence_id": evidence.evidence_id,
            "source_kind": evidence.source_kind,
            "source_ref": evidence.source_ref,
            "text": evidence.text,
            "memory_type": evidence.memory_type,
            "requested_scope": evidence.requested_scope,
            "canonical_key": evidence.canonical_key,
            "value": evidence.value,
            "explicit_persist": evidence.explicit_persist,
            "sensitive": evidence.sensitive,
            "secret": evidence.secret,
            "volatile": evidence.volatile,
            "existing": (
                {
                    "scope": evidence.existing.scope,
                    "canonical_key": evidence.existing.canonical_key,
                    "value": evidence.existing.value,
                }
                if evidence.existing is not None
                else None
            ),
            "resolution": (
                evidence.resolution.to_dict()
                if evidence.resolution is not None
                else None
            ),
        }
        policy_payload = {
            "scope": policy.scope,
            "allow_persist": policy.allow_persist,
            "allowed_memory_types": sorted(policy.allowed_memory_types),
            "authorized_source_kinds": sorted(policy.authorized_source_kinds),
        }
        payload = {
            "evidence": evidence_payload,
            "policy": policy_payload,
        }
        return [
            SystemMessage(content=MEMORY_LEARNING_PROMPT),
            HumanMessage(
                content=json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            ),
        ]

    @staticmethod
    def _schema_from_structured(response: Any) -> _MemoryLearningDecisionSchema:
        if isinstance(response, _MemoryLearningDecisionSchema):
            return response
        if isinstance(response, Mapping):
            return _MemoryLearningDecisionSchema.model_validate(response)
        if hasattr(response, "model_dump"):
            return _MemoryLearningDecisionSchema.model_validate(response.model_dump())
        raise TypeError(
            "structured Memory Learning Provider returned an unsupported object"
        )


__all__ = [
    "MemoryLearningProvider",
    "MemoryLearningSelection",
    "MemoryLearningSelectionError",
    "MemoryLearningSelectionEvidence",
]
