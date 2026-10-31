"""Unified Memory Service and the learned-memory write boundary.

Session and short-term conversation buffers remain runtime-owned state.  Any
durable learned memory (facts, summaries, preferences, and resolutions) is
decided by ``agent.memory.learning`` and committed through
``MemoryPersistenceBoundary``.
"""

from __future__ import annotations

import hashlib
import re
from typing import Optional

from agent.memory.learning import (
    ExistingMemory,
    InteractionEvidence,
    MemoryLearningDecision,
    MemoryPolicyProjection,
    ResolutionEvidence,
    VALID_SCOPES,
    authorize_memory_learning_proposal,
)
from agent.memory.learning_provider import (
    MemoryLearningProvider,
    MemoryLearningSelectionEvidence,
)
from agent.memory.persistence import MemoryCommitEvidence, MemoryPersistenceBoundary




class MemoryLearningResult(dict[str, dict[str, str]]):
    """Committed user facts plus proposal and commit evidence."""

    def __init__(
        self,
        facts: dict[str, dict[str, str]] | None = None,
        *,
        proposals: tuple[MemoryLearningDecision, ...] = (),
        provider_evidence: tuple[MemoryLearningSelectionEvidence, ...] = (),
        commits: tuple[MemoryCommitEvidence, ...] = (),
    ) -> None:
        super().__init__(facts or {})
        self.proposals = proposals
        self.provider_evidence = provider_evidence
        self.commit_evidence = commits

    @property
    def committed(self) -> bool:
        return self.has_committed_evidence

    @property
    def has_committed_evidence(self) -> bool:
        """Whether at least one durable write was confirmed by the boundary."""

        return any(commit.committed for commit in self.commit_evidence)



class MemoryService:
    # ===== Layer 3: Long-Term (ChromaDB semantic + SQLite facts) =====

    @staticmethod
    def retrieve_long_term(
        user_id: str,
        query: str,
        k: int = 5,
        *,
        scope: str = "session",
    ) -> str:
        """Retrieve semantically relevant summaries in one explicit scope."""
        from agent.memory.long_term import retrieve_summaries

        return retrieve_summaries(user_id, query, k=k, scope=scope)

    @staticmethod
    def _learning_policy(
        namespace: str,
        scope: str,
        *,
        authorized_source_kinds: frozenset[str],
    ) -> MemoryPolicyProjection:
        return MemoryPolicyProjection(
            scope=scope,
            namespace=namespace,
            authorized_source_kinds=authorized_source_kinds,
        )

    @staticmethod
    async def _propose_authorize_commit(
        evidence: InteractionEvidence,
        policy: MemoryPolicyProjection,
        provider: MemoryLearningProvider,
    ) -> tuple[
        MemoryLearningDecision,
        MemoryLearningSelectionEvidence,
        MemoryCommitEvidence,
    ]:
        selection = await provider.select_with_evidence(
            evidence,
            evidence.existing,
            policy,
        )
        authorized = authorize_memory_learning_proposal(
            evidence,
            policy,
            selection.decision,
        )
        commit = MemoryPersistenceBoundary.commit(authorized, policy)
        return selection.decision, selection.evidence, commit

    @staticmethod
    async def learn_summary(
        user_id: str,
        summary: str,
        *,
        scope: str = "session",
        evidence_id: Optional[str] = None,
        source_ref: Optional[str] = None,
    ) -> MemoryCommitEvidence:
        """Propose, authorize, and persist a generated conversation summary."""
        normalized = str(summary or "").strip()
        stable_id = evidence_id or (
            "summary-"
            + hashlib.sha256(
                f"{scope}\0{user_id}\0{normalized}".encode("utf-8")
            ).hexdigest()
        )
        evidence = InteractionEvidence(
            evidence_id=stable_id,
            source_kind="conversation_summary",
            source_ref=source_ref or f"summary:{stable_id}",
            text=normalized,
            memory_type="summary",
            requested_scope=scope,
            canonical_key=(
                "summary-"
                + hashlib.sha256(normalized.encode("utf-8")).hexdigest()
            ),
            value=normalized,
            explicit_persist=True,
            sensitive=False,
            secret=False,
            volatile=False,
        )
        policy = MemoryService._learning_policy(
            user_id,
            scope,
            authorized_source_kinds=frozenset({"conversation_summary"}),
        )
        _, _, commit = await MemoryService._propose_authorize_commit(
            evidence,
            policy,
            MemoryLearningProvider(),
        )
        return commit

    # ===== User Facts =====

    @staticmethod
    def get_user_facts(user_id: str, *, scope: str = "user") -> str:
        """Get facts in the requested learning scope as readable text."""
        from agent.memory.long_term import get_facts_text

        return get_facts_text(user_id, scope=scope)

    # ===== Layer 4: Cross-Session Resolution Memory =====

    @staticmethod
    def _filter_negative_context(text: str) -> str:
        """Filter negative/empty results from injected memory context."""
        if not text:
            return text
        negative_patterns = [
            "未找到",
            "无信息",
            "无相关",
            "没有信息",
            "找不到",
            "暂无信息",
            "nothing",
            "no results",
            "not found",
        ]
        lines = text.split("\n")
        filtered = [
            line for line in lines if not any(p in line for p in negative_patterns)
        ]
        result = "\n".join(filtered).strip()
        return result if result else ""

    # ===== Durable learning from user interaction =====

    @staticmethod
    async def learn_from_interaction(
        user_id: str,
        text: str,
        *,
        memory_action: str = "none",
        facts: list | None = None,
        scope: str = "user",
        source_ref: str = "user-input",
    ) -> MemoryLearningResult:
        """Run the canonical Provider → policy → persistence learning path."""
        from agent.memory.long_term import get_fact

        candidates = [
            (str(f.get("category", "personal")), str(f["key"]),
             str(f.get("memory_type", "fact")), str(f["value"]))
            for f in (facts or [])
            if isinstance(f, dict) and f.get("key") and f.get("value") is not None
        ]
        if not candidates:
            return MemoryLearningResult()

        policy = MemoryPolicyProjection(scope=scope, namespace=user_id)
        provider = MemoryLearningProvider()
        evidence_items: list[InteractionEvidence] = []
        selections: list[
            tuple[InteractionEvidence, MemoryLearningDecision, MemoryLearningSelectionEvidence]
        ] = []
        for category, key, memory_type, value in candidates:
            canonical_key = f"{category}.{key}"
            existing_value = get_fact(
                user_id,
                category,
                key,
                scope=scope,
            )
            evidence_id = "interaction-" + hashlib.sha256(
                f"{scope}\0{user_id}\0{source_ref}\0{canonical_key}\0{value}".encode(
                    "utf-8"
                )
            ).hexdigest()
            sensitive = canonical_key in {"personal.phone", "personal.email"}
            secret = canonical_key == "secret.api_key"
            evidence = InteractionEvidence(
                evidence_id=evidence_id,
                source_kind="user_statement",
                source_ref=source_ref,
                text=text,
                memory_type=memory_type,
                requested_scope=scope,
                canonical_key=canonical_key,
                value=value,
                explicit_persist=(memory_action == "remember" if sensitive else True),
                sensitive=sensitive,
                secret=secret,
                volatile=False,
                memory_action=memory_action,
                existing=(
                    ExistingMemory(
                        scope=scope,
                        canonical_key=canonical_key,
                        value=existing_value,
                    )
                    if existing_value is not None
                    else None
                ),
            )
            evidence_items.append(evidence)

        # Complete every Provider proposal before the first durable write, so
        # a Provider/schema failure cannot leave a partially learned turn.
        for evidence in evidence_items:
            selection = await provider.select_with_evidence(
                evidence,
                evidence.existing,
                policy,
            )
            selections.append((evidence, selection.decision, selection.evidence))

        committed_facts: dict[str, dict[str, str]] = {}
        proposals: list[MemoryLearningDecision] = []
        provider_evidence: list[MemoryLearningSelectionEvidence] = []
        commits: list[MemoryCommitEvidence] = []
        for evidence, proposal, proposal_evidence in selections:
            authorized = authorize_memory_learning_proposal(
                evidence,
                policy,
                proposal,
            )
            commit = MemoryPersistenceBoundary.commit(authorized, policy)
            proposals.append(proposal)
            provider_evidence.append(proposal_evidence)
            commits.append(commit)
            if commit.committed:
                category, key = evidence.canonical_key.split(".", 1)
                committed_facts.setdefault(category, {})[key] = evidence.value

        return MemoryLearningResult(
            committed_facts,
            proposals=tuple(proposals),
            provider_evidence=tuple(provider_evidence),
            commits=tuple(commits),
        )

class ScopedMemoryView:
    """Explicitly scoped learned-memory view."""

    def __init__(
        self,
        namespace: str,
        *,
        learning_namespace: str | None = None,
        learning_scope: str = "session",
    ) -> None:
        namespace = str(namespace or "").strip()
        if not namespace:
            raise ValueError("memory namespace must be non-empty")
        learning_namespace = str(learning_namespace or namespace).strip()
        if not learning_namespace:
            raise ValueError("learning namespace must be non-empty")
        if learning_scope not in VALID_SCOPES:
            raise ValueError(f"invalid memory scope: {learning_scope}")
        self.namespace = namespace
        self.learning_namespace = learning_namespace
        self.learning_scope = learning_scope
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError(f"memory view is closed: {self.namespace}")

    def retrieve_long_term(self, query: str, k: int = 5) -> str:
        self._ensure_open()
        return MemoryService.retrieve_long_term(
            self.namespace,
            query,
            k=k,
            scope="session",
        )

    async def learn_summary(self, summary: str) -> MemoryCommitEvidence:
        self._ensure_open()
        return await MemoryService.learn_summary(
            self.namespace,
            summary,
            scope="session",
        )

    def get_user_facts(self) -> str:
        self._ensure_open()
        return MemoryService.get_user_facts(
            self.learning_namespace,
            scope=self.learning_scope,
        )

    def get_retrieved_memory(self, query: str) -> dict[str, str]:
        self._ensure_open()
        return {
            "long_term": MemoryService._filter_negative_context(
                self.retrieve_long_term(query, k=3)
            ) or "",
            "facts": self.get_user_facts() or "",
        }

    async def learn_from_interaction(
        self,
        text: str,
        *,
        memory_action: str = "none",
        facts: list | None = None,
        source_ref: str = "user-input",
    ) -> MemoryLearningResult:
        self._ensure_open()
        return await MemoryService.learn_from_interaction(
            self.learning_namespace,
            text,
            memory_action=memory_action,
            facts=facts,
            scope=self.learning_scope,
            source_ref=source_ref,
        )

    def close(self) -> None:
        self._closed = True


__all__ = ["MemoryLearningResult", "MemoryService", "ScopedMemoryView"]
