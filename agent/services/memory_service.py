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


_INTERROGATIVE_RE = re.compile(r"什么|哪个|哪一|哪儿|哪里|谁|怎么|为什么|吗|呢|？|\?")
_EXPLICIT_PERSIST_RE = re.compile(
    r"记住|保存|存下来|长期|以后|默认|优先|偏好|请",
    re.IGNORECASE,
)
_USER_MEMORY_PATTERNS = (
    (re.compile(r"我(?:叫|是)\s*([^，。！？；;\n]{1,12})"), "personal", "name", "fact"),
    (re.compile(r"我(?:住在|居住于|居住在|来自)\s*([^，。！？；;\n]{1,16})"), "personal", "location", "fact"),
    (
        re.compile(r"(?:记住[:：]?(?:这个)?(?:项目)?|(?:这个)?项目)(?:叫|是|为)\s*([^，。！？；;\n]{1,24})"),
        "misc",
        "project",
        "fact",
    ),
    (
        re.compile(r"我(?:最)?喜欢(?:的)?(?:编程语言|语言)[是为:：]?\s*([^，。！？；;\n]{1,12})"),
        "programming",
        "language",
        "preference",
    ),
    (re.compile(r"我(?:最)?喜欢\s*([^，。！？；;\n]{1,12})"), "hobby", "preference", "preference"),
    (
        re.compile(r"(?:我的\s*)?(?:API[ _-]?key|密钥)\s*(?:是|为|[:：])?\s*(sk-[A-Za-z0-9_-]+)", re.IGNORECASE),
        "secret",
        "api_key",
        "fact",
    ),
    (re.compile(r"(?:我的\s*)?手机号\s*(?:是|为|[:：])?\s*(1\d{10})"), "personal", "phone", "fact"),
    (
        re.compile(r"(?:我的\s*)?(?:邮箱|email)\s*(?:是|为|[:：])?\s*([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})", re.IGNORECASE),
        "personal",
        "email",
        "fact",
    ),
)


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
        return bool(self)


def _user_memory_candidates(text: str) -> list[tuple[str, str, str, str]]:
    if _INTERROGATIVE_RE.search(text):
        return []
    candidates: list[tuple[str, str, str, str]] = []
    seen: set[str] = set()
    skip_hobby = "编程语言" in text or "语言" in text
    for pattern, category, key, memory_type in _USER_MEMORY_PATTERNS:
        if category == "hobby" and skip_hobby:
            continue
        match = pattern.search(text)
        if match is None or not match.group(1).strip():
            continue
        canonical_key = f"{category}.{key}"
        if canonical_key in seen:
            continue
        seen.add(canonical_key)
        candidates.append((category, key, memory_type, match.group(1).strip()))
    return candidates


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
        scope: str = "user",
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
    async def learn_resolution(
        user_id: str,
        utterance: str,
        resolved_target: str,
        kind: str,
        metadata: Optional[dict] = None,
        *,
        scope: str = "user",
    ) -> MemoryCommitEvidence:
        """Propose, authorize, and persist a confirmed resolution fact."""
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
            resolution=ResolutionEvidence(
                utterance=utterance,
                kind=kind,
                metadata=dict(metadata or {}),
            ),
        )
        policy = MemoryService._learning_policy(
            user_id,
            scope,
            authorized_source_kinds=frozenset({"user_confirmed_resolution"}),
        )
        _, _, commit = await MemoryService._propose_authorize_commit(
            evidence,
            policy,
            MemoryLearningProvider(),
        )
        return commit

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

    # ===== Durable learning from user interaction =====

    @staticmethod
    async def learn_from_interaction(
        user_id: str,
        text: str,
        *,
        scope: str = "user",
        source_ref: str = "user-input",
    ) -> MemoryLearningResult:
        """Run the canonical Provider → policy → persistence learning path."""
        candidates = _user_memory_candidates(str(text or ""))
        if not candidates:
            return MemoryLearningResult()

        from agent.memory.long_term import get_fact

        explicit_request = bool(_EXPLICIT_PERSIST_RE.search(text))
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
                explicit_persist=(explicit_request if sensitive else True),
                sensitive=sensitive,
                secret=secret,
                volatile=False,
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

    async def learn_summary(self, summary: str) -> MemoryCommitEvidence:
        self._ensure_open()
        return await MemoryService.learn_summary(
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

    async def learn_resolution(
        self,
        utterance: str,
        resolved_target: str,
        kind: str,
        metadata: dict | None = None,
    ) -> MemoryCommitEvidence:
        self._ensure_open()
        return await MemoryService.learn_resolution(
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

    async def learn_from_interaction(
        self,
        text: str,
        *,
        source_ref: str = "user-input",
    ) -> MemoryLearningResult:
        self._ensure_open()
        return await MemoryService.learn_from_interaction(
            self.namespace,
            text,
            scope=self.learning_scope,
            source_ref=source_ref,
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


__all__ = ["MemoryLearningResult", "MemoryService", "ScopedMemoryView"]
