"""D-3f production wiring for Provider-backed durable Memory learning."""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from agent.context_policy import ContextPolicy
from agent.memory.learning import (
    MemoryLearningDecision,
    decide_memory_learning,
)
from agent.memory.learning_provider import (
    MemoryLearningProvider,
    MemoryLearningSelection,
    MemoryLearningSelectionEvidence,
)


ROOT = Path(__file__).resolve().parents[1]


def _selection(decision: MemoryLearningDecision) -> MemoryLearningSelection:
    return MemoryLearningSelection(
        decision=decision,
        evidence=MemoryLearningSelectionEvidence(
            provider="test-provider",
            provider_path="SINGLE_PROVIDER",
            format_path="STRUCTURED_ONLY",
            raw_output=decision.to_dict(),
        ),
    )


def test_runtime_turn_uses_provider_policy_and_commit_once(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from agent.memory import long_term, preference
    from agent.runtime import UniversalAgent
    from agent.services.memory_service import ScopedMemoryView

    monkeypatch.setattr(long_term, "FACTS_DB_PATH", tmp_path / "facts.db")
    long_term._init_facts_db()
    provider_calls: list[str] = []

    async def select_with_evidence(self, evidence, existing, policy):
        provider_calls.append(evidence.canonical_key)
        assert existing == evidence.existing
        return _selection(decide_memory_learning(evidence, policy))

    async def legacy_extractor_forbidden(_text: str):
        raise AssertionError("legacy fact extractor is not the learning authority")

    monkeypatch.setattr(
        MemoryLearningProvider,
        "select_with_evidence",
        select_with_evidence,
    )
    monkeypatch.setattr(
        preference,
        "extract_facts_with_llm",
        legacy_extractor_forbidden,
    )

    class Orchestrator:
        replan_count = 0

        def reset_timings(self) -> None:
            return None

        def get_timings(self) -> dict[str, float]:
            return {}

        async def plan(self, **_kwargs: Any):
            return {
                "plan": [],
                "requested_outcomes": ["USER_VISIBLE_OUTPUT"],
                "answer_required": True,
            }, "FINISH", "好的。"

        async def finalize(self, **kwargs: Any) -> str:
            return str(kwargs.get("best_answer") or "好的。")

    user_id = "runtime-memory-wiring"
    memory_view = ScopedMemoryView(user_id, learning_scope="user")
    agent = UniversalAgent.__new__(UniversalAgent)
    agent._memory_view = memory_view
    agent._memory_namespace = user_id
    agent._pending_execution_target = ""
    agent._run_context = SimpleNamespace(
        run_id="run-memory-wiring",
        cancellation_view=SimpleNamespace(
            raise_if_requested=lambda *_args, **_kwargs: None
        ),
    )
    agent._timings = {}
    agent._wall_total_seconds = 0.0
    agent._session_context = SimpleNamespace(
        conversation_tracker=SimpleNamespace(update=lambda **_kwargs: None),
        memory_scope="user",
    )
    agent.orchestrator = Orchestrator()
    monkeypatch.setattr(agent, "_print_timing_summary", lambda: {})

    answer = asyncio.run(agent._run_in_context("我叫 TS"))

    assert answer == "好的。"
    assert provider_calls == ["personal.name"]
    assert long_term.get_fact(
        user_id,
        "personal",
        "name",
        scope="user",
    ) == "TS"


def test_secret_provider_proposal_is_vetoed_before_storage(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from agent.memory import long_term
    from agent.services.memory_service import MemoryService

    monkeypatch.setattr(long_term, "FACTS_DB_PATH", tmp_path / "facts.db")
    long_term._init_facts_db()
    provider_calls = 0
    storage_calls = 0

    async def unsafe_store_proposal(self, evidence, existing, policy):
        nonlocal provider_calls
        provider_calls += 1
        assert existing == evidence.existing
        return _selection(
            MemoryLearningDecision(
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
        )

    def persist_forbidden(*_args, **_kwargs):
        nonlocal storage_calls
        storage_calls += 1
        raise AssertionError("secret proposal reached durable storage")

    monkeypatch.setattr(
        MemoryLearningProvider,
        "select_with_evidence",
        unsafe_store_proposal,
    )
    monkeypatch.setattr(long_term, "_persist_fact", persist_forbidden)

    prompt = "我的 API key 是 sk-test-example"
    assert ContextPolicy.for_request(prompt).post_answer_fact_extraction is True
    result = asyncio.run(
        MemoryService.learn_from_interaction(
            "runtime-memory-safety",
            prompt,
            scope="user",
            source_ref="run:runtime-memory-safety",
        )
    )

    assert provider_calls == 1
    assert storage_calls == 0
    assert result == {}
    assert result.committed is False
    assert len(result.proposals) == 1
    assert result.proposals[0].action == "STORE"
    assert len(result.commit_evidence) == 1
    assert result.commit_evidence[0].committed is False
    assert result.commit_evidence[0].reason_code == "SECRET_NEVER_STORE"
    assert long_term.get_facts("runtime-memory-safety", scope="user") == {}


def test_durable_learning_has_one_production_proposal_owner() -> None:
    call_sites: list[tuple[str, str]] = []
    for base in (ROOT / "agent", ROOT / "tools"):
        for path in base.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                function = node.func
                name = (
                    function.id
                    if isinstance(function, ast.Name)
                    else function.attr
                    if isinstance(function, ast.Attribute)
                    else ""
                )
                if name in {"MemoryLearningProvider", "extract_facts_with_llm"}:
                    call_sites.append((str(path.relative_to(ROOT)), name))

    assert call_sites
    assert {path for path, _name in call_sites} == {
        "agent/services/memory_service.py"
    }
    assert {name for _path, name in call_sites} == {"MemoryLearningProvider"}


def test_legacy_learning_entry_is_not_exposed() -> None:
    from agent.services.memory_service import MemoryService, ScopedMemoryView

    assert not hasattr(MemoryService, "extract_and_save_facts")
    assert not hasattr(ScopedMemoryView, "extract_and_save_facts")
