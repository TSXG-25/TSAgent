"""Routing contracts for specialized workflows and memory queries."""

from agent.cognition.cognitive_context import CognitiveContext
from agent.cognition.intent_engine import IntentEngine
from agent.cognition.intent_schema import (
    DOMAIN_DEVELOPMENT,
    DOMAIN_MEMORY,
    IntentResult,
)
from agent.router.workflow_router import WorkflowRouter


def test_programming_language_question_is_memory_query():
    intent = IntentEngine().analyze(CognitiveContext(query="我最喜欢什么编程语言？"))

    assert intent.domain == DOMAIN_MEMORY
    assert not intent.requires_execution


def test_generic_code_request_does_not_enter_question_workflow():
    import workflows.code_generation  # registers the canonical workflow

    router = WorkflowRouter()
    from agent.router.workflow_router import _is_question_code_generation

    router.register_condition(_is_question_code_generation, "code_generation")
    router.register_domain(DOMAIN_DEVELOPMENT, None)

    generic = IntentResult(
        domain=DOMAIN_DEVELOPMENT,
        action="code",
        raw_input="帮我写一个排序函数",
    )
    question = IntentResult(
        domain=DOMAIN_DEVELOPMENT,
        action="code",
        raw_input="根据 input/question.docx 解题并保存为 output/answer.py",
        target="output/answer.py",
    )

    assert router.route(generic)[0] is None
    assert router.route(question)[0] is not None
