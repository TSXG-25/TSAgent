"""Tests for Cognitive Layer — ReferenceResolver + IntentEngine + CognitiveContext.

Covers:
1. ReferenceResolver deterministic rules (pronoun, omitted target, continuation)
2. IntentEngine with context (target merging, entity merging)
3. CognitiveContext construction and properties
4. Full cognitive pipeline integration
"""
import pytest
from dataclasses import dataclass, field
from typing import Optional


# ── Test helper: minimal WorkspaceContext stub ──
@dataclass
class WorkspaceContextStub:
    current_file: Optional[str] = None
    opened_files: list[str] = field(default_factory=list)
    edited_files: list[str] = field(default_factory=list)
    current_symbol: Optional[str] = None
    recent_symbols: list[str] = field(default_factory=list)
    last_symbol: Optional[str] = None

    def record_open(self, path: str): pass
    def record_edit(self, path: str): pass
    def record_symbol(self, symbol: str): pass


# ── Import actual modules ──
from agent.cognition.cognitive_context import CognitiveContext
from agent.cognition.intent_engine import IntentEngine
from agent.cognition.intent_schema import IntentResult, DOMAIN_DEVELOPMENT, DOMAIN_CHAT


@pytest.fixture(autouse=True)
def semantic_fixtures(monkeypatch):
    """These tests exercise context merging, not real Provider classification."""
    from dataclasses import replace
    responses = {
        "帮我看看这个错误日志": IntentResult(domain=DOMAIN_DEVELOPMENT),
        "你好": IntentResult(domain=DOMAIN_CHAT, requires_execution=False),
        "读取 output/solution.py": IntentResult(domain="file", action="read"),
        "优化一下": IntentResult(domain=DOMAIN_DEVELOPMENT, action="modify"),
        "解释这里": IntentResult(domain="knowledge", action="explain", requires_execution=False),
    }
    def analyze(self, text, context):
        return replace(responses[text], raw_input=text)
    monkeypatch.setattr(IntentEngine, '_llm_analyze', analyze)


