"""The single durable boundary for learned Memory writes.

The existing fact, summary, and resolution stores remain the storage
implementations.  Callers may reach them only through this module, which
turns every write into explicit commit evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .learning import MemoryLearningDecision, MemoryPolicyProjection


@dataclass(frozen=True, slots=True)
class MemoryCommitEvidence:
    """Durable result of one learning decision."""

    committed: bool
    action: str
    store: str
    scope: str
    canonical_key: str
    evidence_id: str
    reason_code: str
    record_id: str | None = None
    revision: int | None = None
    error: str | None = None
    # Canonical content projection returned only for a durable commit. This
    # lets callers verify what was written without reading Runtime state.
    content: Mapping[str, object] | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "committed": self.committed,
            "action": self.action,
            "store": self.store,
            "scope": self.scope,
            "canonical_key": self.canonical_key,
            "evidence_id": self.evidence_id,
            "reason_code": self.reason_code,
            "record_id": self.record_id,
            "revision": self.revision,
            "error": self.error,
            "content": dict(self.content) if self.content is not None else None,
        }


def _store_name(memory_type: str) -> str:
    return {
        "fact": "user_facts",
        "preference": "user_facts",
        "summary": "long_term_memory",
        "resolution": "resolution_memory",
    }[memory_type]


def _stable_error(error: Exception) -> str:
    message = str(error).strip() or type(error).__name__
    return f"{type(error).__name__}: {message}"[:300]


def _content_projection(decision: MemoryLearningDecision) -> dict[str, object]:
    if decision.memory_type in {"fact", "preference"}:
        return {"value": decision.value}
    if decision.memory_type == "summary":
        return {"summary": decision.value}
    resolution = decision.resolution
    if resolution is None:
        raise AssertionError("resolution content requires canonical resolution evidence")
    return {
        "utterance": resolution.utterance,
        "resolved_target": decision.value,
        "kind": resolution.kind,
        "metadata": dict(resolution.metadata),
    }


class MemoryPersistenceBoundary:
    """Commit a validated decision and return truthful durable evidence."""

    @staticmethod
    def commit(
        decision: MemoryLearningDecision,
        policy: MemoryPolicyProjection,
    ) -> MemoryCommitEvidence:
        evidence_id = str(decision.provenance.get("evidence_id", ""))
        if decision.action == "IGNORE":
            return MemoryCommitEvidence(
                committed=False,
                action=decision.action,
                store="",
                scope="",
                canonical_key="",
                evidence_id=evidence_id,
                reason_code=decision.reason_code,
            )

        if decision.scope != policy.scope:
            return MemoryCommitEvidence(
                committed=False,
                action=decision.action,
                store="",
                scope=decision.scope,
                canonical_key=decision.canonical_key,
                evidence_id=evidence_id,
                reason_code="SCOPE_WIDENING_DENIED",
                error="decision scope does not match persistence policy scope",
            )

        store_name = _store_name(decision.memory_type)
        if decision.memory_type == "resolution" and decision.resolution is None:
            return MemoryCommitEvidence(
                committed=False,
                action=decision.action,
                store=store_name,
                scope=decision.scope,
                canonical_key=decision.canonical_key,
                evidence_id=evidence_id,
                reason_code=decision.reason_code,
                error="resolution decision requires canonical resolution evidence",
            )
        try:
            receipt: dict[str, Any]
            if decision.memory_type in {"fact", "preference"}:
                from agent.memory.long_term import _persist_fact

                category, key = decision.canonical_key.split(".", 1)
                receipt = _persist_fact(
                    policy.namespace,
                    category,
                    key,
                    decision.value,
                    scope=decision.scope,
                    action=decision.action,
                    evidence_id=evidence_id,
                    source_kind=decision.provenance["source_kind"],
                    source_ref=decision.provenance["source_ref"],
                )
            elif decision.memory_type == "summary":
                from agent.memory.long_term import _persist_summary

                receipt = _persist_summary(
                    policy.namespace,
                    decision.value,
                    scope=decision.scope,
                    canonical_key=decision.canonical_key,
                    evidence_id=evidence_id,
                    source_kind=decision.provenance["source_kind"],
                    source_ref=decision.provenance["source_ref"],
                )
            else:
                from agent.memory.resolution import _persist_resolution

                resolution = decision.resolution
                if resolution is None:
                    raise AssertionError("resolution evidence checked before persistence")
                receipt = _persist_resolution(
                    policy.namespace,
                    resolution.utterance,
                    decision.value,
                    resolution.kind,
                    scope=decision.scope,
                    canonical_key=decision.canonical_key,
                    evidence_id=evidence_id,
                    source_kind=decision.provenance["source_kind"],
                    source_ref=decision.provenance["source_ref"],
                    metadata=dict(resolution.metadata),
                )
        except Exception as error:
            return MemoryCommitEvidence(
                committed=False,
                action=decision.action,
                store=store_name,
                scope=decision.scope,
                canonical_key=decision.canonical_key,
                evidence_id=evidence_id,
                reason_code=decision.reason_code,
                error=_stable_error(error),
            )

        return MemoryCommitEvidence(
            committed=True,
            action=decision.action,
            store=store_name,
            scope=decision.scope,
            canonical_key=decision.canonical_key,
            evidence_id=evidence_id,
            reason_code=decision.reason_code,
            record_id=str(receipt["record_id"]),
            revision=(
                int(receipt["revision"])
                if receipt.get("revision") is not None
                else None
            ),
            content=_content_projection(decision),
        )


__all__ = ["MemoryCommitEvidence", "MemoryPersistenceBoundary"]
