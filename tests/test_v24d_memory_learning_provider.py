from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path
from types import SimpleNamespace

from agent.memory.learning import (
    ExistingMemory,
    InteractionEvidence,
    MemoryLearningDecision,
    MemoryPolicyProjection,
    authorize_memory_learning_proposal,
)
from agent.memory.learning_provider import (
    MemoryLearningProvider,
    MemoryLearningSelectionError,
)
from realtest_reports.harness.v24d_memory_learning_provider_preflight import (
    build_preflight_report,
)


ROOT = Path(__file__).resolve().parents[1]


def _evidence(*, existing: ExistingMemory | None = None) -> InteractionEvidence:
    return InteractionEvidence(
        evidence_id="ev-provider-1",
        source_kind="user_statement",
        source_ref="turn:provider-1",
        text="我现在改用 Neovim。",
        memory_type="preference",
        requested_scope="user",
        canonical_key="programming.editor",
        value="Neovim",
        explicit_persist=True,
        sensitive=False,
        secret=False,
        volatile=False,
        existing=existing,
    )


def _policy() -> MemoryPolicyProjection:
    return MemoryPolicyProjection(scope="user", namespace="provider-test")


def _decision_payload(
    *,
    action: str = "STORE",
    scope: str = "user",
    canonical_key: str = "programming.editor",
    value: str = "Neovim",
) -> dict[str, object]:
    if action == "IGNORE":
        return {
            "action": "IGNORE",
            "memory_type": "",
            "scope": "",
            "canonical_key": "",
            "value": "",
            "provenance": {},
            "reason_code": "MODEL_DECIDED_NOT_TO_LEARN",
        }
    return {
        "action": action,
        "memory_type": "preference",
        "scope": scope,
        "canonical_key": canonical_key,
        "value": value,
        "provenance": {
            "evidence_id": "ev-provider-1",
            "source_kind": "user_statement",
            "source_ref": "turn:provider-1",
        },
        "reason_code": "PROVIDER_PROPOSAL",
    }


class _StructuredProvider:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.structured_calls = 0
        self.raw_calls = 0
        self.messages = None

    def with_structured_output(self, _schema):
        provider = self

        class Runnable:
            async def ainvoke(self, messages):
                provider.structured_calls += 1
                provider.messages = messages
                return dict(provider.payload)

        return Runnable()

    async def ainvoke(self, _messages):
        self.raw_calls += 1
        raise AssertionError("raw path must not run after structured success")


class _RawProvider:
    def __init__(self, content: str, *, structured_error: bool = False) -> None:
        self.content = content
        self.structured_error = structured_error
        self.structured_calls = 0
        self.raw_calls = 0

    def with_structured_output(self, _schema):
        provider = self

        class Runnable:
            async def ainvoke(self, _messages):
                provider.structured_calls += 1
                if provider.structured_error:
                    raise RuntimeError("response_format unsupported")
                raise AssertionError("structured path was not expected")

        return Runnable()

    async def ainvoke(self, _messages):
        self.raw_calls += 1
        return SimpleNamespace(content=self.content)


def test_provider_has_the_narrow_production_signature() -> None:
    parameters = inspect.signature(MemoryLearningProvider.select).parameters

    assert tuple(parameters) == ("self", "evidence", "existing_memory", "policy")
    assert inspect.iscoroutinefunction(MemoryLearningProvider.select)


def test_structured_provider_returns_uncommitted_canonical_decision() -> None:
    provider = _StructuredProvider(_decision_payload())
    selector = MemoryLearningProvider(provider=provider, provider_name="deepseek")
    evidence = _evidence()
    policy = _policy()

    selection = asyncio.run(
        selector.select_with_evidence(evidence, None, policy)
    )

    assert selection.decision == MemoryLearningDecision(
        action="STORE",
        memory_type="preference",
        scope="user",
        canonical_key="programming.editor",
        value="Neovim",
        provenance={
            "evidence_id": "ev-provider-1",
            "source_kind": "user_statement",
            "source_ref": "turn:provider-1",
        },
        reason_code="PROVIDER_PROPOSAL",
    )
    assert selection.evidence.provider == "deepseek"
    assert selection.evidence.provider_path == "SINGLE_PROVIDER"
    assert selection.evidence.format_path == "STRUCTURED_ONLY"
    assert provider.structured_calls == 1
    assert provider.raw_calls == 0
    assert provider.messages is not None
    assert "Memory Learning Provider" in provider.messages[0].content
    assert "provider-test" not in provider.messages[1].content


def test_provider_keeps_existing_memory_in_the_narrow_projection() -> None:
    existing = ExistingMemory(
        scope="user",
        canonical_key="programming.editor",
        value="Vim",
    )
    provider = _StructuredProvider(_decision_payload(action="UPDATE"))
    selector = MemoryLearningProvider(provider=provider, provider_name="deepseek")

    selection = asyncio.run(
        selector.select_with_evidence(_evidence(), existing, _policy())
    )

    assert selection.decision.action == "UPDATE"
    assert '"value":"Vim"' in provider.messages[1].content


def test_provider_does_not_accept_conflicting_existing_projection() -> None:
    evidence_existing = ExistingMemory(
        scope="user",
        canonical_key="programming.editor",
        value="Vim",
    )
    supplied_existing = ExistingMemory(
        scope="user",
        canonical_key="programming.editor",
        value="Emacs",
    )
    selector = MemoryLearningProvider(
        provider=_StructuredProvider(_decision_payload(action="UPDATE")),
        provider_name="deepseek",
    )

    try:
        asyncio.run(
            selector.select_with_evidence(
                _evidence(existing=evidence_existing),
                supplied_existing,
                _policy(),
            )
        )
    except ValueError as error:
        assert str(error) == (
            "existing memory projection does not match evidence.existing"
        )
    else:
        raise AssertionError("conflicting projections must fail before Provider call")


def test_format_fallback_is_same_provider_and_not_a_retry() -> None:
    provider = _RawProvider(
        json.dumps(_decision_payload(action="IGNORE")),
        structured_error=True,
    )
    selector = MemoryLearningProvider(provider=provider, provider_name="deepseek")

    selection = asyncio.run(
        selector.select_with_evidence(_evidence(), None, _policy())
    )

    assert selection.decision.action == "IGNORE"
    assert selection.evidence.provider_path == "SINGLE_PROVIDER"
    assert selection.evidence.format_path == "STRUCTURED_TO_RAW_FALLBACK"
    assert provider.structured_calls == 1
    assert provider.raw_calls == 1


def test_invalid_raw_response_is_not_repaired() -> None:
    provider = _RawProvider(
        '```json\n{"action":"IGNORE"}\n```',
    )
    selector = MemoryLearningProvider(
        provider=provider,
        provider_name="ollama",
        supports_structured_output=False,
    )

    try:
        asyncio.run(selector.select(_evidence(), None, _policy()))
    except MemoryLearningSelectionError as error:
        assert error.code == "SCHEMA_INVALID"
        assert error.provider == "ollama"
        assert error.format_path == "RAW_ONLY"
    else:
        raise AssertionError("non-canonical raw output must fail")


def test_deterministic_authorization_vetoes_provider_scope_widening() -> None:
    evidence = _evidence()
    policy = _policy()
    proposal = MemoryLearningDecision(
        action="STORE",
        memory_type="preference",
        scope="repository",
        canonical_key="programming.editor",
        value="Neovim",
        provenance={
            "evidence_id": "ev-provider-1",
            "source_kind": "user_statement",
            "source_ref": "turn:provider-1",
        },
        reason_code="PROVIDER_PROPOSAL",
    )

    authorized = authorize_memory_learning_proposal(evidence, policy, proposal)

    assert authorized.action == "IGNORE"
    assert authorized.reason_code == "PROVIDER_PROPOSAL_REJECTED"


def test_policy_veto_wins_over_provider_write_proposal() -> None:
    evidence = InteractionEvidence(
        evidence_id="ev-secret",
        source_kind="user_statement",
        source_ref="turn:secret",
        text="请记住我的 API key。",
        memory_type="fact",
        requested_scope="user",
        canonical_key="secret.api_key",
        value="sk-example",
        explicit_persist=True,
        sensitive=True,
        secret=True,
        volatile=False,
    )
    proposal = MemoryLearningDecision(
        action="STORE",
        memory_type="fact",
        scope="user",
        canonical_key="secret.api_key",
        value="sk-example",
        provenance={
            "evidence_id": "ev-secret",
            "source_kind": "user_statement",
            "source_ref": "turn:secret",
        },
        reason_code="PROVIDER_PROPOSAL",
    )

    authorized = authorize_memory_learning_proposal(
        evidence,
        MemoryPolicyProjection(scope="user", namespace="secret-test"),
        proposal,
    )

    assert authorized.action == "IGNORE"
    assert authorized.reason_code == "SECRET_NEVER_STORE"


def test_provider_source_has_no_store_or_evaluation_imports() -> None:
    source = (ROOT / "agent/memory/learning_provider.py").read_text(encoding="utf-8")

    assert "from agent.memory.persistence" not in source
    assert "from agent.memory.long_term" not in source
    assert "from agent.memory.resolution" not in source
    assert "from agent.services.memory_service" not in source
    assert "from evals" not in source
    assert "from evaluation" not in source


def test_provider_preflight_unlocks_d3_without_calling_provider_or_store() -> None:
    report = build_preflight_report()

    assert report["status"] == "READY_FOR_REAL_BASELINE"
    assert len(report["production_entries"]["provider_learning"]) == 1
    assert report["production_entries"]["provider_boundary_violations"] == []
    assert report["case_reports"] == []
    assert report["configuration"]["provider_calls"] == 0
    assert report["configuration"]["memory_writes"] == 0
