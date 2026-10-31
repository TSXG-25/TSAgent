"""H1a regressions for natural memory entry, recall, and truthful acknowledgement."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.cognition.cognitive_context import CognitiveContext
from agent.cognition.intent_engine import IntentEngine
from tests.test_ha_r1_harness import ScriptedProvider, intent
from agent.cognition.intent_schema import DOMAIN_MEMORY
from agent.memory.learning import decide_memory_learning
from agent.memory.learning_provider import (
    MemoryLearningSelection,
    MemoryLearningSelectionEvidence,
    MemoryLearningProvider,
)
from agent.memory.persistence import MemoryCommitEvidence
from agent.services.memory_service import (
    MemoryLearningResult,
    MemoryService,
)



def test_implicit_memory_intent_uses_llm_analysis() -> None:
    questions = (
        "我平时 Python 项目偏好用什么？",
        "我 Python 平时用什么工具？",
    )
    for text in questions:
        provider = ScriptedProvider([(text, intent(text, domain='记忆', action='recall'))])
        engine = IntentEngine()
        engine._llm = provider
        result = asyncio.run(engine.analyze_async(CognitiveContext(query=text)))
        assert result.domain == DOMAIN_MEMORY, text
        assert result.requires_execution is False, text

def _committed_result() -> MemoryLearningResult:
    return MemoryLearningResult(
        {"programming": {"python_environment": "uv"}},
        commits=(
            MemoryCommitEvidence(
                committed=True,
                action="STORE",
                store="user_facts",
                scope="user",
                canonical_key="programming.python_environment",
                evidence_id="evidence-h1a",
                reason_code="EXPLICIT_USER_PREFERENCE",
                record_id="1",
                revision=1,
                content={"value": "uv"},
            ),
        ),
    )

