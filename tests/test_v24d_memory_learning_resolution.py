"""D-3e resolution evidence contract and commit truthfulness tests."""

from __future__ import annotations

from pathlib import Path

from agent.memory.learning import (
    InteractionEvidence,
    MemoryLearningDecision,
    MemoryPolicyProjection,
    ResolutionEvidence,
    authorize_memory_learning_proposal,
    decide_memory_learning,
)
from agent.memory.persistence import MemoryPersistenceBoundary
from evals.memory_learning.oracle import (
    DATASET_V11_VERSION,
    dataset_hash,
    load_dataset,
    load_dataset_v11,
)


BASE_DATASET_HASH = "e821b67e7da66b40a7c1cc38ac6e18d1636b6d01c6aeaa3e873f03bb95f928b7"
V11_DATASET_HASH = "c3b4ed509f6629674c4476f00bf0026561c1f5b1ceb54084ca1bca7443692e82"


def _resolution_evidence(case: dict) -> InteractionEvidence:
    raw = case["evidence"]
    resolution = raw.get("resolution")
    assert isinstance(resolution, dict)
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
        resolution=ResolutionEvidence(
            utterance=resolution["utterance"],
            kind=resolution["kind"],
            metadata=dict(resolution["metadata"]),
        ),
    )


def _proposal_from_case(case: dict) -> MemoryLearningDecision:
    raw = case["evidence"]
    return MemoryLearningDecision(
        action="STORE",
        memory_type=raw["memory_type"],
        scope=raw["requested_scope"],
        canonical_key=raw["canonical_key"],
        value=raw["value"],
        provenance={
            "evidence_id": raw["id"],
            "source_kind": raw["source_kind"],
            "source_ref": raw["source_ref"],
        },
        reason_code="PROVIDER_PROPOSAL",
    )


def test_v11_dataset_adds_only_the_explicit_resolution_payload() -> None:
    old = load_dataset()
    payload = load_dataset_v11()

    assert old["version"] != DATASET_V11_VERSION
    assert len(payload["cases"]) == 24
    assert payload["base_dataset_hash"] == BASE_DATASET_HASH
    assert dataset_hash(payload) == V11_DATASET_HASH
    resolution_case = next(case for case in payload["cases"] if case["id"] == "D023")
    assert resolution_case["evidence"]["resolution"] == {
        "utterance": "请记住我刚打开的是 agent/runtime.py。",
        "kind": "file",
        "metadata": {},
    }


def test_resolution_payload_survives_policy_projection() -> None:
    case = next(case for case in load_dataset_v11()["cases"] if case["id"] == "D023")
    evidence = _resolution_evidence(case)
    decision = decide_memory_learning(
        evidence,
        MemoryPolicyProjection(scope="session", namespace="resolution-projection"),
    )

    assert decision.to_dict() == case["expected"]
    assert decision.resolution == evidence.resolution


def test_sensitive_provider_proposals_are_vetoed_before_durable_commit(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from agent.memory import long_term

    monkeypatch.setattr(long_term, "FACTS_DB_PATH", tmp_path / "facts.db")
    long_term._init_facts_db()
    payload = load_dataset_v11()
    for case_id, reason_code in (
        ("D018", "SENSITIVE_WITHOUT_EXPLICIT_PERSISTENCE"),
        ("D020", "SECRET_NEVER_STORE"),
    ):
        case = next(case for case in payload["cases"] if case["id"] == case_id)
        raw = case["evidence"]
        evidence = InteractionEvidence(
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
        )
        policy = MemoryPolicyProjection(
            scope=raw["requested_scope"],
            namespace=f"safety-{case_id.lower()}",
        )
        authorized = authorize_memory_learning_proposal(
            evidence,
            policy,
            _proposal_from_case(case),
        )
        commit = MemoryPersistenceBoundary.commit(authorized, policy)

        assert authorized.action == "IGNORE"
        assert authorized.reason_code == reason_code
        assert commit.committed is False
        assert commit.record_id is None
        assert long_term.get_facts(policy.namespace, scope=policy.scope) == {}
