"""Unified Memory Service and the learned-memory write boundary.

Session and short-term conversation buffers remain runtime-owned state.  Any
durable learned memory (facts, summaries, preferences, and resolutions) is
decided by ``agent.memory.learning`` and committed through
``MemoryPersistenceBoundary``.
"""

from __future__ import annotations

import hashlib
from typing import Optional

from agent.memory.learning import (
    InteractionEvidence,
    MemoryPolicyProjection,
    VALID_SCOPES,
    decide_memory_learning,
)
from agent.memory.persistence import MemoryCommitEvidence, MemoryPersistenceBoundary


class MemoryService:
    # ===== Layer 1: Session (in-memory, always on) =====

    @staticmethod
    def record_user_message(user_id: str, content: str) -> None:
        """Record user message in session memory."""
        from agent.memory.session import add_user_message

        add_user_message(user_id, content)

    @staticmethod
    def record_assistant_message(user_id: str, content: str) -> None:
        """Record assistant message in session memory."""
        from agent.memory.session import add_assistant_message

        add_assistant_message(user_id, content)

    @staticmethod
    def get_session_context(user_id: str, n: int = 10) -> str:
        """Get current session conversation history."""
        from agent.memory.session import get_session_context as _get

        return _get(user_id, n=n)

    # ===== Layer 2: Short-Term (persistent, compressed) =====

    @staticmethod
    def add_exchange(
        user_id: str,
        user_input: str,
        assistant_response: str,
        *,
        scope: str = "user",
    ) -> None:
        """Record an exchange; compression may create a durable summary."""
        from agent.memory.short_term import add_exchange as _add

        _add(user_id, user_input, assistant_response, scope=scope)

    @staticmethod
    def get_short_term_history(user_id: str, n: int = 6) -> str:
        """Get recent short-term conversation history."""
        from agent.memory.short_term import get_history as _get

        return _get(user_id, n=n)

    # ===== Layer 3: Long-Term (ChromaDB semantic + SQLite facts) =====

    @staticmethod
    def retrieve_long_term(
        user_id: str,
        query: str,
        k: int = 5,
        *,
        scope: str = "user",
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
    def store_summary(
        user_id: str,
        summary: str,
        *,
        scope: str = "user",
        evidence_id: Optional[str] = None,
        source_ref: Optional[str] = None,
    ) -> MemoryCommitEvidence:
        """Authorize and persist a generated conversation summary."""
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
        decision = decide_memory_learning(evidence, policy)
        return MemoryPersistenceBoundary.commit(decision, policy)

    # ===== User Facts =====

    @staticmethod
    def get_user_facts(user_id: str, *, scope: str = "user") -> str:
        """Get facts in the requested learning scope as readable text."""
        from agent.memory.long_term import get_facts_text

        return get_facts_text(user_id, scope=scope)

    # ===== Layer 4: Cross-Session Resolution Memory =====

    @staticmethod
    def record_resolution(
        user_id: str,
        utterance: str,
        resolved_target: str,
        kind: str,
        metadata: Optional[dict] = None,
        *,
        scope: str = "user",
    ) -> MemoryCommitEvidence:
        """Authorize and persist a confirmed resolution fact."""
        canonical_key = f"resolution.{kind}.{resolved_target}"
        evidence_id = "resolution-" + hashlib.sha256(
            f"{scope}\0{user_id}\0{canonical_key}".encode("utf-8")
        ).hexdigest()
        evidence = InteractionEvidence(
            evidence_id=evidence_id,
            source_kind="user_confirmed_resolution",
            source_ref=f"resolution:{evidence_id}",
            text=utterance,
            memory_type="resolution",
            requested_scope=scope,
            canonical_key=canonical_key,
            value=resolved_target,
            explicit_persist=True,
            sensitive=False,
            secret=False,
            volatile=False,
            attributes={
                "utterance": utterance,
                "kind": kind,
                "metadata": dict(metadata or {}),
            },
        )
        policy = MemoryService._learning_policy(
            user_id,
            scope,
            authorized_source_kinds=frozenset({"user_confirmed_resolution"}),
        )
        decision = decide_memory_learning(evidence, policy)
        return MemoryPersistenceBoundary.commit(decision, policy)

    @staticmethod
    def get_resolutions(
        user_id: str,
        n: int = 20,
        *,
        scope: str = "user",
    ) -> list:
        """Get recent resolution facts in one explicit scope."""
        from agent.memory.resolution import get_resolutions as _get

        return _get(user_id, n=n, scope=scope)

    # ===== Unified Interface =====

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

    @staticmethod
    def get_context(
        user_id: str,
        query: str,
        *,
        scope: str = "user",
    ) -> dict[str, str]:
        """Get session, short-term, long-term, and fact context."""
        return {
            "session": MemoryService.get_session_context(user_id, n=8) or "",
            "short_term": MemoryService._filter_negative_context(
                MemoryService.get_short_term_history(user_id, n=5)
            )
            or "",
            "long_term": MemoryService._filter_negative_context(
                MemoryService.retrieve_long_term(user_id, query, k=3, scope=scope)
            )
            or "",
            "facts": MemoryService.get_user_facts(user_id, scope=scope) or "",
        }

    # ===== Fact Extraction =====

    @staticmethod
    async def extract_and_save_facts(
        user_id: str,
        text: str,
        *,
        scope: str = "user",
    ) -> dict:
        """Extract facts and commit only policy-approved durable facts."""
        from agent.memory.preference import async_extract_and_save_facts

        return await async_extract_and_save_facts(user_id, text, scope=scope)

    # ===== Record Full Exchange =====

    @staticmethod
    def record_full_exchange(
        user_id: str,
        user_input: str,
        assistant_response: str,
        *,
        scope: str = "user",
    ) -> None:
        """Record a complete exchange in session and short-term memory."""
        MemoryService.record_user_message(user_id, user_input)
        MemoryService.record_assistant_message(user_id, assistant_response)
        MemoryService.add_exchange(
            user_id,
            user_input,
            assistant_response,
            scope=scope,
        )


class ScopedMemoryView:
    """Session-owned memory projection with an explicit learning scope."""

    def __init__(self, namespace: str, *, learning_scope: str = "session") -> None:
        namespace = str(namespace or "").strip()
        if not namespace:
            raise ValueError("memory namespace must be non-empty")
        if learning_scope not in VALID_SCOPES:
            raise ValueError(f"invalid memory scope: {learning_scope}")
        self.namespace = namespace
        self.learning_scope = learning_scope
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError(f"memory view is closed: {self.namespace}")

    def record_user_message(self, content: str) -> None:
        self._ensure_open()
        MemoryService.record_user_message(self.namespace, content)

    def record_assistant_message(self, content: str) -> None:
        self._ensure_open()
        MemoryService.record_assistant_message(self.namespace, content)

    def get_session_context(self, n: int = 10) -> str:
        self._ensure_open()
        return MemoryService.get_session_context(self.namespace, n=n)

    def add_exchange(self, user_input: str, assistant_response: str) -> None:
        self._ensure_open()
        MemoryService.add_exchange(
            self.namespace,
            user_input,
            assistant_response,
            scope=self.learning_scope,
        )

    def get_short_term_history(self, n: int = 6) -> str:
        self._ensure_open()
        return MemoryService.get_short_term_history(self.namespace, n=n)

    def retrieve_long_term(self, query: str, k: int = 5) -> str:
        self._ensure_open()
        return MemoryService.retrieve_long_term(
            self.namespace,
            query,
            k=k,
            scope=self.learning_scope,
        )

    def store_summary(self, summary: str) -> MemoryCommitEvidence:
        self._ensure_open()
        return MemoryService.store_summary(
            self.namespace,
            summary,
            scope=self.learning_scope,
        )

    def get_user_facts(self) -> str:
        self._ensure_open()
        return MemoryService.get_user_facts(
            self.namespace,
            scope=self.learning_scope,
        )

    def record_resolution(
        self,
        utterance: str,
        resolved_target: str,
        kind: str,
        metadata: dict | None = None,
    ) -> MemoryCommitEvidence:
        self._ensure_open()
        return MemoryService.record_resolution(
            self.namespace,
            utterance,
            resolved_target,
            kind,
            metadata,
            scope=self.learning_scope,
        )

    def get_resolutions(self, n: int = 20) -> list:
        self._ensure_open()
        return MemoryService.get_resolutions(
            self.namespace,
            n=n,
            scope=self.learning_scope,
        )

    def get_context(self, query: str) -> dict[str, str]:
        self._ensure_open()
        return MemoryService.get_context(
            self.namespace,
            query,
            scope=self.learning_scope,
        )

    async def extract_and_save_facts(self, text: str) -> dict:
        self._ensure_open()
        return await MemoryService.extract_and_save_facts(
            self.namespace,
            text,
            scope=self.learning_scope,
        )

    def record_full_exchange(self, user_input: str, assistant_response: str) -> None:
        self._ensure_open()
        MemoryService.record_full_exchange(
            self.namespace,
            user_input,
            assistant_response,
            scope=self.learning_scope,
        )

    def close(self) -> None:
        self._closed = True


__all__ = ["MemoryService", "ScopedMemoryView"]
