"""H4a: preserve explicit execution outcomes before Planner routing."""

from agent.cognition.cognitive_context import CognitiveContext, ResolvedQuery
from agent.cognition.execution_need import (
    RequestedOutcome,
    analyze_execution_need,
    analyze_requested_outcomes,
)
from agent.cognition.intent_engine import engine
from agent.cognition.intent_schema import IntentResult


def test_explicit_python_execution_is_not_plain_math() -> None:
    outcomes = analyze_requested_outcomes(
        "用 Python 算 1+1 并实际执行，把输出贴出来"
    )

    assert RequestedOutcome.CODE_EXECUTION in outcomes
    assert RequestedOutcome.USER_VISIBLE_OUTPUT in outcomes
    assert analyze_execution_need("用 Python 算 1+1 并实际执行") is True


def test_explicit_command_execution_is_preserved() -> None:
    outcomes = analyze_requested_outcomes("执行 date 命令，原样贴输出")

    assert RequestedOutcome.COMMAND_EXECUTION in outcomes
    assert RequestedOutcome.USER_VISIBLE_OUTPUT in outcomes
    assert analyze_execution_need("执行 date 命令") is True


def test_write_script_and_run_requires_code_execution() -> None:
    outcomes = analyze_requested_outcomes(
        "写一个脚本到 output/probe.py 并运行，贴出输出"
    )

    assert RequestedOutcome.FILE_MUTATION in outcomes
    assert RequestedOutcome.CODE_EXECUTION in outcomes


def test_explanation_does_not_require_execution() -> None:
    outcomes = analyze_requested_outcomes("解释 Python 如何计算 1+1")

    assert outcomes == (RequestedOutcome.USER_VISIBLE_OUTPUT,)
    assert analyze_execution_need("解释 Python 如何计算 1+1") is False


def test_plain_output_request_is_not_command_execution() -> None:
    outcomes = analyze_requested_outcomes("请原样输出这段文字")

    assert RequestedOutcome.COMMAND_EXECUTION not in outcomes


def test_intent_cannot_drop_explicit_execution_when_llm_says_math(
    monkeypatch,
) -> None:
    def fake_llm(_text: str, _context: CognitiveContext) -> IntentResult:
        return IntentResult(
            domain="math",
            action="calculate",
            requires_execution=False,
        )

    monkeypatch.setattr(engine, "_llm_analyze", fake_llm)
    text = "用 Python 算 1+1 并实际执行"
    context = CognitiveContext(
        query=text,
        resolved_query=ResolvedQuery(raw=text),
    )

    intent = engine.analyze(context)

    assert intent.requires_execution is True
    assert RequestedOutcome.CODE_EXECUTION in intent.requested_outcomes
    assert RequestedOutcome.USER_VISIBLE_OUTPUT in intent.requested_outcomes
