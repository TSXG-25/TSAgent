"""Deterministic v2.4D-2 learning authorization/commit boundary tests."""

from __future__ import annotations

import json
import asyncio
from pathlib import Path

import pytest

from agent.memory.learning import (
    ExistingMemory,
    InteractionEvidence,
    MemoryPolicyProjection,
    decide_memory_learning,
)
from agent.memory.persistence import MemoryPersistenceBoundary
from agent.memory.learning_provider import (
    MemoryLearningSelection,
    MemoryLearningSelectionEvidence,
)


ROOT = Path(__file__).resolve().parents[1]
DATASET_PATH = ROOT / "evals" / "memory_learning" / "dataset.json"


def _install_policy_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """Use the frozen policy decision as a deterministic Provider proposal."""

    async def select_with_evidence(self, evidence, existing, policy):
        assert existing == evidence.existing
        decision = decide_memory_learning(evidence, policy)
        return MemoryLearningSelection(
            decision=decision,
            evidence=MemoryLearningSelectionEvidence(
                provider="test-provider",
                provider_path="SINGLE_PROVIDER",
                format_path="STRUCTURED_ONLY",
                raw_output=decision.to_dict(),
            ),
        )

    monkeypatch.setattr(
        "agent.memory.learning_provider.MemoryLearningProvider.select_with_evidence",
        select_with_evidence,
    )


def _evidence_from_case(case: dict) -> InteractionEvidence:
    raw = case["evidence"]
    existing = (
        ExistingMemory(**raw["existing"])
        if raw["existing"] is not None
        else None
    )
    return InteractionEvidence(
        evidence_id=raw["id"],
        source_kind=raw["source_kind"],
        source_ref=raw["source_ref"],
        text=raw["text"],
        memory_type=raw["memory_type"],
        requested_scope=raw["requested_scope"],
        canonical_key=raw["canonical_key"],
        value=raw["value"],
        explicit_persist=raw["explicit_persist"],
        sensitive=raw["sensitive"],
        secret=raw["secret"],
        volatile=raw["volatile"],
        existing=existing,
    )


def _fact_evidence(
    *,
    user_id: str,
    value: str,
    evidence_id: str,
    scope: str = "user",
    existing: ExistingMemory | None = None,
) -> tuple[InteractionEvidence, MemoryPolicyProjection]:
    evidence = InteractionEvidence(
        evidence_id=evidence_id,
        source_kind="user_statement",
        source_ref=f"test:{evidence_id}",
        text=f"我住在{value}",
        memory_type="fact",
        requested_scope=scope,
        canonical_key="personal.location",
        value=value,
        explicit_persist=True,
        sensitive=False,
        secret=False,
        volatile=False,
        existing=existing,
    )
    return evidence, MemoryPolicyProjection(scope=scope, namespace=user_id)


def test_production_policy_matches_all_frozen_dataset_decisions() -> None:
    payload = json.loads(DATASET_PATH.read_text(encoding="utf-8"))

    for case in payload["cases"]:
        evidence = _evidence_from_case(case)
        policy = MemoryPolicyProjection(
            scope=evidence.requested_scope,
            namespace=f"dataset-{case['id']}",
        )
        decision = decide_memory_learning(evidence, policy)
        assert decision.to_dict() == case["expected"], case["id"]


def test_secret_decision_does_not_call_persistence(monkeypatch) -> None:
    evidence = InteractionEvidence(
        evidence_id="secret-1",
        source_kind="user_statement",
        source_ref="test:secret",
        text="请记住我的 API key",
        memory_type="fact",
        requested_scope="user",
        canonical_key="secret.api_key",
        value="sk-example",
        explicit_persist=True,
        sensitive=True,
        secret=True,
        volatile=False,
    )
    policy = MemoryPolicyProjection(scope="user", namespace="secret-test")
    decision = decide_memory_learning(evidence, policy)

    def fail_if_store_called(*_args, **_kwargs):
        raise AssertionError("IGNORE must not reach a storage adapter")

    monkeypatch.setattr(
        MemoryPersistenceBoundary,
        "commit",
        staticmethod(fail_if_store_called),
    )
    assert decision.action == "IGNORE"
    assert decision.to_dict()["provenance"] == {}
    assert decision.reason_code == "SECRET_NEVER_STORE"

    # The boundary itself is also storage-free for IGNORE.
    monkeypatch.undo()
    receipt = MemoryPersistenceBoundary.commit(decision, policy)
    assert receipt.committed is False
    assert receipt.store == ""
    assert receipt.record_id is None


@pytest.mark.parametrize(
    ("canonical_key", "value", "reason_code"),
    [
        ("secret.api_key", "sk-example", "SECRET_NEVER_STORE"),
        ("personal.phone", "13800000000", "SENSITIVE_WITHOUT_EXPLICIT_PERSISTENCE"),
        ("market.btc_price", "100000 USD", "VOLATILE_OBSERVATION"),
    ],
)
def test_policy_rechecks_safety_when_projection_flags_are_missing(
    canonical_key: str,
    value: str,
    reason_code: str,
) -> None:
    evidence = InteractionEvidence(
        evidence_id="inferred-safety",
        source_kind="user_statement",
        source_ref="test:inferred-safety",
        text="普通事实陈述",
        memory_type="fact",
        requested_scope="user",
        canonical_key=canonical_key,
        value=value,
        explicit_persist=False,
        sensitive=False,
        secret=False,
        volatile=False,
    )
    decision = decide_memory_learning(
        evidence,
        MemoryPolicyProjection(scope="user", namespace="inferred-safety"),
    )
    assert decision.action == "IGNORE"
    assert decision.reason_code == reason_code


def test_fact_store_has_truthful_commit_and_idempotent_duplicate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from agent.memory import long_term

    monkeypatch.setattr(long_term, "FACTS_DB_PATH", tmp_path / "facts.db")
    long_term._init_facts_db()
    user_id = "commit-test"

    evidence, policy = _fact_evidence(
        user_id=user_id,
        value="北京",
        evidence_id="fact-1",
    )
    first_decision = decide_memory_learning(evidence, policy)
    first = MemoryPersistenceBoundary.commit(first_decision, policy)
    assert first.committed is True
    assert first.record_id is not None
    assert first.revision == 1
    assert long_term.get_fact(
        user_id, "personal", "location", scope="user"
    ) == "北京"

    duplicate_evidence, duplicate_policy = _fact_evidence(
        user_id=user_id,
        value="北京",
        evidence_id="fact-2",
        existing=ExistingMemory(
            scope="user",
            canonical_key="personal.location",
            value="北京",
        ),
    )
    duplicate = MemoryPersistenceBoundary.commit(
        decide_memory_learning(duplicate_evidence, duplicate_policy),
        duplicate_policy,
    )
    assert duplicate.committed is False
    assert duplicate.reason_code == "DUPLICATE_SAME_VALUE"
    assert long_term.get_fact(
        user_id, "personal", "location", scope="user"
    ) == "北京"


def test_fact_update_increments_revision_and_preserves_scope(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from agent.memory import long_term

    monkeypatch.setattr(long_term, "FACTS_DB_PATH", tmp_path / "facts.db")
    long_term._init_facts_db()
    user_id = "revision-test"

    first_evidence, policy = _fact_evidence(
        user_id=user_id,
        value="北京",
        evidence_id="fact-before",
    )
    assert MemoryPersistenceBoundary.commit(
        decide_memory_learning(first_evidence, policy),
        policy,
    ).committed

    update_evidence, update_policy = _fact_evidence(
        user_id=user_id,
        value="上海",
        evidence_id="fact-after",
        existing=ExistingMemory(
            scope="user",
            canonical_key="personal.location",
            value="北京",
        ),
    )
    updated = MemoryPersistenceBoundary.commit(
        decide_memory_learning(update_evidence, update_policy),
        update_policy,
    )
    assert updated.committed is True
    assert updated.action == "UPDATE"
    assert updated.revision == 2
    assert long_term.get_fact(
        user_id, "personal", "location", scope="user"
    ) == "上海"


def test_same_key_can_exist_in_different_explicit_scopes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from agent.memory import long_term

    monkeypatch.setattr(long_term, "FACTS_DB_PATH", tmp_path / "facts.db")
    long_term._init_facts_db()
    user_id = "scope-test"

    user_evidence, user_policy = _fact_evidence(
        user_id=user_id,
        value="用户值",
        evidence_id="scope-user",
        scope="user",
    )
    session_evidence, session_policy = _fact_evidence(
        user_id=user_id,
        value="会话值",
        evidence_id="scope-session",
        scope="session",
    )
    assert MemoryPersistenceBoundary.commit(
        decide_memory_learning(user_evidence, user_policy),
        user_policy,
    ).committed
    assert MemoryPersistenceBoundary.commit(
        decide_memory_learning(session_evidence, session_policy),
        session_policy,
    ).committed

    assert long_term.get_fact(
        user_id, "personal", "location", scope="user"
    ) == "用户值"
    assert long_term.get_fact(
        user_id, "personal", "location", scope="session"
    ) == "会话值"


def test_persistence_failure_returns_nonempty_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.memory import long_term

    evidence, policy = _fact_evidence(
        user_id="failure-test",
        value="失败值",
        evidence_id="failure-1",
    )
    decision = decide_memory_learning(evidence, policy)

    def fail(*_args, **_kwargs):
        raise RuntimeError()

    monkeypatch.setattr(long_term, "_persist_fact", fail)
    result = MemoryPersistenceBoundary.commit(decision, policy)
    assert result.committed is False
    assert result.error == "RuntimeError: RuntimeError"
    assert result.record_id is None


def test_interaction_learning_returns_only_durable_commits(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from agent.memory import long_term
    from agent.services.memory_service import MemoryService

    monkeypatch.setattr(long_term, "FACTS_DB_PATH", tmp_path / "facts.db")
    long_term._init_facts_db()
    _install_policy_provider(monkeypatch)

    def fail(*_args, **_kwargs):
        raise RuntimeError()

    monkeypatch.setattr(long_term, "_persist_fact", fail)
    result = asyncio.run(
        MemoryService.learn_from_interaction(
            "truthful-ack",
            "我叫小明",
            scope="user",
            source_ref="run:truthful-ack",
        )
    )
    assert result == {}
    assert result.committed is False
    assert len(result.commit_evidence) == 1
    assert result.commit_evidence[0].committed is False
    assert result.commit_evidence[0].error == "RuntimeError: RuntimeError"


def test_save_fact_tool_does_not_claim_unconfirmed_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.services.memory_service import MemoryLearningResult, MemoryService
    from tools.memory import save_fact

    async def fake_learn(*_args, **_kwargs) -> MemoryLearningResult:
        return MemoryLearningResult()

    monkeypatch.setattr(MemoryService, "learn_from_interaction", fake_learn)
    answer = save_fact("我叫小明", user_id="truthful-tool")
    assert "未确认保存" in answer
    assert "事实已保存" not in answer


def test_scope_mismatch_cannot_reach_storage(monkeypatch: pytest.MonkeyPatch) -> None:
    evidence, policy = _fact_evidence(
        user_id="scope-mismatch",
        value="不应写入",
        evidence_id="scope-mismatch-1",
        scope="user",
    )
    decision = decide_memory_learning(evidence, policy)

    from agent.memory import long_term

    def fail(*_args, **_kwargs):
        raise AssertionError("scope mismatch reached storage")

    monkeypatch.setattr(long_term, "_persist_fact", fail)
    mismatched_policy = MemoryPolicyProjection(
        scope="session",
        namespace="scope-mismatch",
    )
    result = MemoryPersistenceBoundary.commit(decision, mismatched_policy)
    assert result.committed is False
    assert result.reason_code == "SCOPE_WIDENING_DENIED"


@pytest.mark.parametrize(
    ("text", "reason_code"),
    [
        (
            "我的手机号是 13800000000。",
            "SENSITIVE_WITHOUT_EXPLICIT_PERSISTENCE",
        ),
        (
            "请记住我的 API key sk-example。",
            "SECRET_NEVER_STORE",
        ),
    ],
)
def test_interaction_projection_cannot_persist_unsafe_facts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    text: str,
    reason_code: str,
) -> None:
    from agent.memory import long_term
    from agent.memory.learning import MemoryLearningDecision
    from agent.memory.learning_provider import MemoryLearningProvider
    from agent.services.memory_service import MemoryService

    monkeypatch.setattr(long_term, "FACTS_DB_PATH", tmp_path / "facts.db")
    long_term._init_facts_db()

    async def unsafe_proposal(self, evidence, existing, policy):
        assert existing == evidence.existing
        decision = MemoryLearningDecision(
            action="STORE",
            memory_type=evidence.memory_type,
            scope=evidence.requested_scope,
            canonical_key=evidence.canonical_key,
            value=evidence.value,
            provenance={
                "evidence_id": evidence.evidence_id,
                "source_kind": evidence.source_kind,
                "source_ref": evidence.source_ref,
            },
            reason_code="PROVIDER_PROPOSED_STORE",
        )
        return MemoryLearningSelection(
            decision=decision,
            evidence=MemoryLearningSelectionEvidence(
                provider="unsafe-test-provider",
                provider_path="SINGLE_PROVIDER",
                format_path="STRUCTURED_ONLY",
                raw_output=decision.to_dict(),
            ),
        )

    monkeypatch.setattr(MemoryLearningProvider, "select_with_evidence", unsafe_proposal)
    result = asyncio.run(
        MemoryService.learn_from_interaction(
            "safety-test",
            text,
            scope="user",
            source_ref="run:safety-test",
        )
    )

    assert result == {}
    assert len(result.commit_evidence) == 1
    assert result.commit_evidence[0].committed is False
    assert result.commit_evidence[0].reason_code == reason_code
    assert long_term.get_facts("safety-test", scope="user") == {}


def test_explicit_sensitive_fact_has_commit_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from agent.memory import long_term
    from agent.services.memory_service import MemoryService

    monkeypatch.setattr(long_term, "FACTS_DB_PATH", tmp_path / "facts.db")
    long_term._init_facts_db()
    _install_policy_provider(monkeypatch)
    result = asyncio.run(
        MemoryService.learn_from_interaction(
            "sensitive-test",
            "请记住我的邮箱 a@example.com。",
            scope="user",
            source_ref="run:sensitive-test",
        )
    )

    assert result == {"personal": {"email": "a@example.com"}}
    assert result.commit_evidence[0].committed is True
    assert result.commit_evidence[0].reason_code == "EXPLICIT_SENSITIVE_PERSISTENCE"


def test_fact_classification_does_not_apply_secret_flag_to_other_fields(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from agent.memory import long_term
    from agent.memory.learning import MemoryLearningDecision
    from agent.memory.learning_provider import MemoryLearningProvider
    from agent.services.memory_service import MemoryService

    monkeypatch.setattr(long_term, "FACTS_DB_PATH", tmp_path / "facts.db")
    long_term._init_facts_db()

    async def store_proposal(self, evidence, existing, policy):
        assert existing == evidence.existing
        decision = MemoryLearningDecision(
            action="STORE",
            memory_type=evidence.memory_type,
            scope=evidence.requested_scope,
            canonical_key=evidence.canonical_key,
            value=evidence.value,
            provenance={
                "evidence_id": evidence.evidence_id,
                "source_kind": evidence.source_kind,
                "source_ref": evidence.source_ref,
            },
            reason_code="PROVIDER_PROPOSED_STORE",
        )
        return MemoryLearningSelection(
            decision=decision,
            evidence=MemoryLearningSelectionEvidence(
                provider="test-provider",
                provider_path="SINGLE_PROVIDER",
                format_path="STRUCTURED_ONLY",
                raw_output=decision.to_dict(),
            ),
        )

    monkeypatch.setattr(MemoryLearningProvider, "select_with_evidence", store_proposal)
    result = asyncio.run(
        MemoryService.learn_from_interaction(
            "mixed-facts",
            "我是小明，API key 是 sk-example。",
            scope="user",
            source_ref="run:mixed-facts",
        )
    )

    assert result == {"personal": {"name": "小明"}}
    reasons = {
        evidence.reason_code for evidence in result.commit_evidence
    }
    assert "EXPLICIT_USER_FACT" in reasons
    assert "SECRET_NEVER_STORE" in reasons
    assert long_term.get_facts("mixed-facts", scope="user") == {
        "personal": {"name": "小明"}
    }


def test_summary_commit_is_scoped_and_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.memory import long_term
    from agent.services.memory_service import MemoryService

    class FakeStore:
        def __init__(self) -> None:
            self.documents: dict[str, object] = {}
            self.add_calls: list[tuple[object, ...]] = []

        def get(self, *, ids=None, where=None):
            if ids is not None:
                return {"ids": [record_id for record_id in ids if record_id in self.documents]}
            return {"ids": [], "documents": [], "metadatas": []}

        def add_documents(self, documents, ids):
            self.add_calls.append(tuple(ids))
            for record_id, document in zip(ids, documents):
                self.documents[record_id] = document

    store = FakeStore()
    monkeypatch.setattr(long_term, "_get_store", lambda: store)
    _install_policy_provider(monkeypatch)

    first = asyncio.run(
        MemoryService.learn_summary(
            "summary-test", "用户偏好简洁回答", scope="user"
        )
    )
    second = asyncio.run(
        MemoryService.learn_summary(
            "summary-test", "用户偏好简洁回答", scope="user"
        )
    )

    assert first.committed is True
    assert second.committed is True
    assert first.record_id == second.record_id
    assert len(store.add_calls) == 1
    stored = next(iter(store.documents.values()))
    assert stored.metadata["scope"] == "user"
    assert stored.metadata["evidence_id"] == first.evidence_id


def test_resolution_commit_is_idempotent_for_same_scoped_fact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from agent.memory import resolution
    from agent.services.memory_service import MemoryService

    monkeypatch.setattr(resolution, "RM_DIR", tmp_path)
    _install_policy_provider(monkeypatch)
    first = asyncio.run(
        MemoryService.learn_resolution(
            "resolution-test", "打开刚才的文件", "output/report.md", "file"
        )
    )
    second = asyncio.run(
        MemoryService.learn_resolution(
            "resolution-test", "继续使用刚才的文件", "output/report.md", "file"
        )
    )

    assert first.committed is True
    assert second.committed is True
    assert first.record_id == second.record_id
    entries = resolution.get_resolutions("resolution-test", scope="user")
    assert len(entries) == 1
    assert entries[0]["resolved_target"] == "output/report.md"
    assert entries[0]["scope"] == "user"


def test_scoped_summary_retrieval_does_not_retry_without_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.memory import long_term

    class FailingScopedStore:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def similarity_search_with_score(self, *_args, **kwargs):
            self.calls.append(dict(kwargs))
            raise RuntimeError("unsupported scoped query")

    store = FailingScopedStore()
    monkeypatch.setattr(long_term, "_get_store", lambda: store)

    assert long_term.retrieve_summaries(
        "scope-test", "previous preference", scope="user"
    ) == ""
    assert store.calls == [
        {"k": 5, "filter": {"$and": [{"user_id": "scope-test"}, {"scope": "user"}]}}
    ]
