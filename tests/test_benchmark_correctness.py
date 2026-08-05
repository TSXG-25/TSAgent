"""ADR-0014 evaluator contracts and cross-module diagnostic checks."""

import pytest

from agent.conversation import (
    ConversationRetrieverProtocol,
    conversation_retriever,
)
from agent.diagnostics import (
    ContractIntegrationError,
    clear_contract_violations,
    get_contract_violations,
    handle_contract_violation,
)
from benchmarks.memory.cases import MemoryCase, build_cases, build_continuation_cases
from benchmarks.memory.metadata import benchmark_metadata
from benchmarks.memory.runner import summarize_results, validate


def test_validator_canonicalizes_and_rejects_forbidden_false_positive():
    case = MemoryCase(
        id="validator-01",
        group="conversation",
        sub="contract",
        turns=["写一个排序程序"],
        expected="unused-legacy-field",
        expected_any_of=[["快排", "快速排序"]],
        forbidden_any_of=[["没有实现", "未完成"]],
        positive_examples=["结果：快速排序"],
        negative_examples=["我没有实现这个任务。"],
    )

    assert validate(case, "结果：快速排序") == (True, "ok")
    # NFKC/case/whitespace normalization makes this a valid positive.
    assert validate(case, "结果：  快速排序。") == (True, "ok")
    # A correct keyword embedded in an explicit failure must not pass.
    passed, detail = validate(case, "我没有实现快速排序")
    assert not passed and "forbidden" in detail
    assert not validate(case, "我不知道")[0]


def test_correct_abstention_can_define_its_own_positive_answer():
    case = MemoryCase(
        id="validator-abstain-01",
        group="memory",
        sub="abstention",
        turns=["我住在哪里？"],
        expected="unused-legacy-field",
        expected_any_of=[["我不知道", "未记录"]],
        positive_examples=["我不知道。"],
        negative_examples=["你住在北京。"],
    )
    assert validate(case, "我不知道。")[0]
    assert not validate(case, "你住在北京。")[0]


def test_benchmark_metadata_contains_reproducible_provenance():
    cases = [
        MemoryCase(
            id="metadata-01",
            group="fact",
            sub="single",
            turns=["我住在北京。"],
            expected="北京",
        )
    ]
    first = benchmark_metadata(cases)
    second = benchmark_metadata(cases)
    assert first == second
    assert first["benchmark_version"]
    assert len(first["dataset_hash"]) == 64
    assert first["case_count"] == 1


def test_continuation_cases_are_structured_and_split_from_recall():
    memory = build_cases(n_per_sub=10, fill_turns=1)
    continuation = build_continuation_cases(n_per_sub=10)
    assert len(memory) == 130
    assert len([case for case in memory if case.group == "continuation"]) == 10
    assert {case.sub for case in continuation} == {
        "plan_resume", "chat_resume", "reference_resume",
    }
    assert all(case.validation_mode == "runtime_contract" for case in continuation)
    assert all(not case.expected for case in continuation)


def test_runtime_continuation_validator_uses_evidence_not_answer_keywords():
    plan = next(case for case in build_continuation_cases() if case.sub == "plan_resume")
    evidence = {
        "conversation_intent": "continue_plan",
        "requires_execution": True,
        "execution_progress": 1,
        "verified_success": True,
    }
    assert validate(plan, "回答里没有平方", evidence=evidence)[0]
    evidence["verified_success"] = False
    assert not validate(plan, "平方", evidence=evidence)[0]


def test_runner_reports_continuation_metrics_separately():
    metrics = summarize_results([
        {
            "group": "continuation", "sub": "plan_resume",
            "metric_scope": "continuation", "passed": True, "exc": None,
        },
        {
            "group": "continuation", "sub": "plan_resume",
            "metric_scope": "continuation", "passed": False,
            "exc": "TimeoutError: network",
        },
        {
            "group": "conversation", "sub": "recent_goal",
            "metric_scope": "memory_recall", "passed": True, "exc": None,
        },
    ])
    by_metric = {(item["metric_scope"], item["metric"]): item
                 for item in metrics["metrics"]}
    plan = by_metric[("continuation", "plan_resume")]
    assert plan["passed"] == 1
    assert plan["exceptions"] == 1
    assert plan["non_exception_pass_rate"] == 100.0
    assert ("memory_recall", "conversation/recent_goal") in by_metric


def test_retriever_satisfies_static_boundary():
    assert isinstance(conversation_retriever, ConversationRetrieverProtocol)


def test_contract_failure_is_visible_in_strict_mode(monkeypatch):
    clear_contract_violations()
    monkeypatch.setenv("TSAGENT_STRICT_CONTRACTS", "1")
    cause = AttributeError("runtime_pending missing")
    with pytest.raises(ContractIntegrationError):
        handle_contract_violation(
            boundary="planner",
            operation="conversation_contract",
            expected="ConversationRetrieverProtocol.runtime_pending",
            error=cause,
        )
    events = get_contract_violations()
    assert events and events[-1].symptom == "contract_violation"
    assert "runtime_pending" in events[-1].failure
    clear_contract_violations()
